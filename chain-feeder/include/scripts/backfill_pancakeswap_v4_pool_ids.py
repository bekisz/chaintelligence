#!/usr/bin/env python3
"""
One-shot backfill that repairs ``liquidity_pool.pool_id`` / ``pool_address`` for
PancakeSwap V4 pools.

Why
---
PancakeSwap V4 (Infinity) uses a singleton PoolManager; a "pool" is identified by
a ``bytes32`` poolId on-chain, but the PancakeSwap V4 subgraph indexes each
``Pool`` entity with a 20-byte (40-hex / 42-char with 0x) surrogate id — and the
PancakeSwap UI's pool-detail page keys off that 42-char id:

    https://pancakeswap.finance/liquidity/pool/<chain>/<pool_id>

459 of 775 BNB rows store a fabricated 66-char value where the subgraph (and the
UI) expect a real 42-char id, so every PancakeSwap arrow on ``/pool`` 404s /
shows nothing.  This script backfills the real id from the V4 subgraph by
matching the row's token pair + fee tier, disambiguating multi-tier pairs by
which pool actually swapped inside the row's active date window.

Run
---
    python chain-feeder/include/scripts/backfill_pancakeswap_v4_pool_ids.py          # dry-run
    python chain-feeder/include/scripts/backfill_pancakeswap_v4_pool_ids.py --apply
"""

import argparse
import os
import sys
import logging
import datetime
import psycopg2
import requests
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger('v4_backfill')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))
from common.utils.config import DATA_WAREHOUSE_DB  # noqa: E402

GATEWAY = 'https://gateway-arbitrum.network.thegraph.com/api/{key}/subgraphs/id/{sub}'
# PancakeSwap V4 BNB subgraph (see backfill_pool_identifiers.py).
V4_SUBGRAPH = '7XgdLW3bts4HktCYsu9dy8bEnuiNeZuftcuK3Aj4JXYV'
_HEADERS = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}


def _epoch(d) -> int:
    if isinstance(d, (datetime.day, datetime.daytime)):
        return int(datetime.daytime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp())
    return int(d)


def fetch_subgraph_pools():
    key = os.getenv('GRAPH_API_KEY', '')
    url = GATEWAY.format(key=key, sub=V4_SUBGRAPH)
    pools, last_id = [], '0x0000000000000000000000000000000000000000000000000000000000000000'
    q = ('query($l:Bytes!){ pools(first:1000, where:{id_gt:$l}, orderBy:id, orderDirection:asc){ '
         'id token0{id symbol} token1{id symbol} feeTier totalValueLockedUSD txCount } }')
    while True:
        try:
            r = requests.post(url, json={'query': q, 'variables': {'l': last_id}},
                               headers=_HEADERS, timeout=60)
            r.raise_for_status()
            batch = r.json().get('data', {}).get('pools', [])
        except Exception as e:
            log.warning('subgraph page failed (%s); retrying', e); continue
        if not batch:
            break
        for p in batch:
            try:
                pools.append({
                    'id': p['id'].lower(),
                    't0': p['token0']['id'].lower(),
                    't1': p['token1']['id'].lower(),
                    'fee': int(p['feeTier']) // 100,
                    'tvl': float(p.get('totalValueLockedUSD') or 0.0),
                    'tx_count': int(p.get('txCount') or 0),
                })
            except (TypeError, ValueError):
                pass
        last_id = batch[-1]['id']
        if len(batch) < 1000:
            break
    return pools, url


def resolve_pool_by_swaps(subgraph_url, date_lo, date_hi, candidates):
    """Return the candidate pool id that had a swap inside [date_lo, date_hi]."""
    if not subgraph_url or not candidates or not date_lo:
        return None
    lo, hi = _epoch(date_lo), _epoch(date_hi) + 86399
    fields, keys = [], []
    for i, addr in enumerate(candidates[:4]):
        f = f'p{i}'
        fields.append(f'{f}: swaps(first:1, orderBy:timestamp, orderDirection:desc, '
                      f'where:{{pool:"{addr}", timestamp_gte:{lo}, timestamp_lte:{hi}}}){{ pool{{id}} }}')
        keys.append(f)
    q = '{ ' + ' '.join(fields) + ' }'
    try:
        r = requests.post(subgraph_url, json={'query': q}, headers=_HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json().get('data', {}) or {}
    except Exception as e:
        log.warning('swaps-lookup failed (%s)', e); return None
    for k, addr in zip(keys, candidates[:4]):
        if data.get(k):
            return addr
    return None


def fetch_rows(net):
    conn = psycopg2.connect(DATA_WAREHOUSE_DB); cur = conn.cursor()
    cur.execute("""
        SELECT lp.id, lp.pool_name, lp.fee_bps, LOWER(COALESCE(lp.pool_id,'')),
               LOWER(COALESCE(lp.pool_address,'')),
               cc0.contract_address, cc1.contract_address,
               (SELECT MIN(h.day) FROM liquidity_pool_daily_stats h WHERE h.pool_id=lp.id),
               (SELECT MAX(h.day) FROM liquidity_pool_daily_stats h WHERE h.pool_id=lp.id),
               lp.chain_id, lp.protocol_id
        FROM liquidity_pool lp
        JOIN chain ch ON lp.chain_id=ch.id
        JOIN protocol pr ON lp.protocol_id=pr.id
        LEFT JOIN coin_contract cc0 ON cc0.coin_id=lp.coin0_id AND cc0.chain_id=lp.chain_id
        LEFT JOIN coin_contract cc1 ON cc1.coin_id=lp.coin1_id AND cc1.chain_id=lp.chain_id
        WHERE ch.name=%s AND pr.name='PancakeSwap V4'
    """, (net,))
    rows = cur.fetchall()
    cur.execute("SELECT chain_id, protocol_id, pool_name, fee_bps, COALESCE(pool_id,'') FROM liquidity_pool")
    existing = set(cur.fetchall())
    conn.close()
    return rows, existing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--network', default='BNB')
    args = ap.parse_args()

    pools, sub_url = fetch_subgraph_pools()
    by_addr = {p['id']: p for p in pools}
    by_pair = defaultdict(list)
    for p in pools:
        by_pair[frozenset((p['t0'], p['t1']))].append(p)
    log.info('subgraph: %d V4 pools indexed', len(pools))

    rows, existing = fetch_rows(args.network)
    log.info('%s: %d PancakeSwap V4 rows', args.network, len(rows))

    changes, stats = [], defaultdict(int)
    for cid, pname, fee_bps, stored_pid, stored_paddr, a0, a1, d_lo, d_hi, ch_id, pr_id in rows:
        if not a0 or not a1:
            stats['NO_TOKEN_ADDR'] += 1; continue
        # already real?
        if stored_pid and stored_pid in by_addr:
            stats['ALREADY_REAL'] += 1; continue
        sg = by_pair.get(frozenset((a0.lower(), a1.lower())), [])
        if not sg:
            stats['NO_SUBGRAPH_PAIR'] += 1; continue
        sg_tiers = defaultdict(list)
        for p in sg:
            sg_tiers[p['fee']].append(p)
        # prefer the tier matching the row's fee_bps
        picked = None
        if fee_bps is not None and int(fee_bps) in sg_tiers:
            cand = sg_tiers[int(fee_bps)]
            picked = cand[0] if len(cand) == 1 else None
        if picked is None and len(sg) == 1:
            picked = sg[0]
        if picked is None and sg:
            # multi-tier, ambiguous -> authoritative swap-window lookup
            cand_ids = [p['id'] for p in sg]
            hit = resolve_pool_by_swaps(sub_url, d_lo, d_hi, cand_ids)
            if hit and hit in by_addr:
                picked = by_addr[hit]
        if picked is None:
            stats['UNRESOLVED'] += 1; continue
        new_id = picked['id']
        if stored_pid == new_id:
            stats['NO_CHANGE'] += 1; continue
        collides = (ch_id, pr_id, pname, fee_bps, new_id) in existing and \
            (ch_id, pr_id, pname, fee_bps, stored_pid) != (ch_id, pr_id, pname, fee_bps, new_id)
        changes.append((cid, stored_pid or '', new_id, 'COLLIDE' if collides else 'OK'))
        stats[('COLLIDE' if collides else 'FIX')] += 1

    for k, v in sorted(stats.items()):
        log.info('  %-16s %d', k, v)
    safe = [c for c in changes if c[3] == 'OK']
    blocked = [c for c in changes if c[3] == 'COLLIDE']
    log.info('CHANGES: %d safe, %d colliding, %d total', len(safe), len(blocked), len(changes))

    if args.apply and safe:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB); cur = conn.cursor()
        cur.execute('BEGIN')
        try:
            for cid, _old, new_id, _ in safe:
                cur.execute("""UPDATE liquidity_pool SET pool_id=%s, pool_address=%s
                               WHERE id=%s AND pool_id IS DISTINCT FROM %s""",
                            (new_id, new_id, cid, new_id))
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
        log.info('APPLIED %d updates', len(safe))
    elif changes:
        log.info('dry-run; re-run with --apply. sample:')
        for c in safe[:12]:
            log.info('  cid=%-7s %s -> %s', c[0], c[1][:14] or '(none)', c[2])
        if blocked:
            log.info('  colliding (skipped): %d', len(blocked))
    else:
        log.info('nothing to change')


if __name__ == '__main__':
    main()
