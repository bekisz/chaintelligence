#!/usr/bin/env python3
"""
One-shot backfill that corrects ``liquidity_pool.fee_bps`` for Uniswap V3 pools.

Why this exists
---------------
``liquidity_pool.fee_bps`` is wrong for a large share of the long tail of V3
rows.  On a 40-row Ethereum V3 sample, ~37% had no real pool at the recorded
fee tier, and for most of those a real pool exists at a *different* tier.  A
wrong ``fee_bps`` makes the locally-derived CREATE2 pool address (used for the
Uniswap / Revert / DexScreener links on ``/pool``) point at nothing, so the
links 404 even after the address-derivation fix.

How it fixes it
----------------
The Uniswap V3 subgraph indexes ``Pool.feeTier`` authoritatively (in hundredths
of a bip — divide by 100 for basis points), which breaks the chicken-and-egg:
we don't need to know the fee to find the pool, the subgraph hands us the fee
of every pool in bulk.

Matching priority per row (token contract addresses come from ``coin_contract``):

  1. ADDR_MATCH   — the row's stored ``pool_address`` is a real subgraph pool id.
                     Its ``feeTier`` is the authoritative fee.  Also repairs
                     ``pool_address`` to the checksummed subgraph id.
  2. FEE_OK       — stored address is junk, but the row's current ``fee_bps``
                     *is* one of the pair's real subgraph fee tiers.  Fee is
                     already correct; only the address was fabricated (the
                     CREATE2 derivation in postgres_fetcher.py already repairs
                     that at read time, so no change needed here).
  3. FEE_FIX_ONE  — stored fee matches no subgraph tier for the pair AND the
                     pair has exactly one subgraph pool.  Unambiguous: set the
                     fee to that pool's tier.
  4. FEE_AMBIG    — pair has several subgraph pools and the row's fee/address
                     match none of them.  Disambiguate by closest
                     ``totalValueLockedUSD`` to the row's ``avg_tvl`` (the pool
                     our history tracked is the one whose TVL profile matches).
  5. NO_SUBGRAPH  — the pair has no subgraph pool at all.  Left untouched.

Networks without a V3 subgraph id on hand (BNB) fall back to on-chain CREATE2
probing: for each unique derivable pair we check which standard fee tiers
(1/5/25/30/100 bps depending on protocol) deploy a real contract via
``eth_getCode`` and pick the unique tier, or disambiguate by TVL when several
exist.

Run
---
    # dry run (no writes) — default
    python chain-feeder/include/scripts/backfill_pool_fee_bps.py
    # apply
    python chain-feeder/include/scripts/backfill_pool_fee_bps.py --apply
    # restrict to a network/protocol
    python ... --network Ethereum --protocol "Uniswap V3"
"""

import argparse
import os
import sys
import logging
import time
import json
import datetime
import psycopg2
import requests
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger("fee_backfill")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))

from common.utils.config import DATA_WAREHOUSE_DB  # noqa: E402

GATEWAY = 'https://gateway-arbitrum.network.thegraph.com/api/{key}/subgraphs/id/{sub}'


def _epoch(d) -> int:
    """Python date -> unix epoch seconds at UTC midnight (subgraph 'timestamp' is unix seconds)."""
    if isinstance(d, (datetime.day, datetime.daytime)):
        return int(datetime.daytime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp())
    return int(d)


def resolve_pool_by_swaps(subgraph_url, date_lo, date_hi, candidates):
    """Authoritative disambiguation: return the candidate pool id that actually
    had a swap inside [date_lo, date_hi], else None.  ``candidates`` is a list of
    lowercase pool addresses.  One subgraph request aliases a swaps() field per
    candidate so we learn which pool was active in the row's window."""
    if not subgraph_url or not candidates or not date_lo:
        return None
    lo, hi = _epoch(date_lo), _epoch(date_hi) + 86399   # inclusive end-of-day
    fields, keys = [], []
    for i, addr in enumerate(candidates[:4]):
        f = f'p{i}'
        fields.append(f'{f}: swaps(first:1, orderBy:timestamp, orderDirection:desc, '
                      f'where:{{pool:"{addr}", timestamp_gte:{lo}, timestamp_lte:{hi}}}){{ pool{{id feeTier}} }}')
        keys.append(f)
    q = '{ ' + ' '.join(fields) + ' }'
    try:
        r = requests.post(subgraph_url, json={'query': q}, headers=_HEADERS, timeout=45)
        r.raise_for_status()
        data = r.json().get('data', {}) or {}
    except Exception as e:
        log.warning('swaps-lookup failed (%s); candidates=%s', e, candidates)
        return None
    for k, addr in zip(keys, candidates[:4]):
        rows = data.get(k) or []
        if rows:
            return rows[0]['pool']['id'].lower()
    return None

# Uniswap V3 subgraph deployment ids (from include/uniswap_v3_range_fetcher.py).
# BNB and Base fall back to the on-chain path: BNB has no hosted V3 subgraph
# here, and the Base subgraph HMuAwufq… is flaky and has served non-standard
# feeTiers (200/400) from a fork/bad indexer, so it is not trusted.
UNISWAP_V3_SUBGRAPHS = {
    'Ethereum': '5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV',
    'Arbitrum': '3V7ZY6muhxaQL5qvntX1CFXJ32W7BxXZTGTwmpH5J4t3',
}

# Standard V3 fee tiers in basis points, per protocol family.  The on-chain
# CREATE2 probe tries each of these for pairs we cannot resolve from a subgraph.
V3_TIERS_BY_PROTOCOL = {
    'Uniswap V3':     [1, 5, 30, 100],
    'PancakeSwap V3': [1, 5, 25, 100],
}

# Public Ethereum-family RPC endpoints used only for the BNB on-chain fallback.
# The hosted .env RPC_URL_ETHEREUM is malformed (two comma-joined URLs) and
# Ankr now requires a paid key, so we rotate across these.
PUBLIC_RPCS = {
    'Ethereum': [
        'https://ethereum-rpc.publicnode.com', 'https://eth.merkle.io',
        'https://rpc.flashbots.net', 'https://eth.drpc.org',
    ],
    'Arbitrum': ['https://arbitrum-one-rpc.publicnode.com', 'https://arbitrum.drpc.org'],
    'Base':    ['https://base-rpc.publicnode.com', 'https://base.drpc.org'],
    'BNB':     ['https://bsc-rpc.publicnode.com', 'https://bsc-dataseed.bnbchain.org',
                'https://bsc.drpc.org'],
}

_HEADERS = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}


# --------------------------------------------------------------------------- subgraph
def fetch_subgraph_pools(subgraph_id: str):
    """Page through every Pool of a V3 subgraph (ordered by id) and return a list
    of dicts: {id, t0, t1, fee, tvl, tx_count} with addresses lower-cased and
    fee in basis points (feeTier is hundredths-of-a-bip → /100)."""
    key = os.getenv('GRAPH_API_KEY', '')
    if not key:
        raise RuntimeError('GRAPH_API_KEY not set')
    url = GATEWAY.format(key=key, sub=subgraph_id)
    pools, last_id, page = [], '', 0
    q = ('query($last: ID!) { pools(first: 1000, where: { id_gt: $last }, '
         'orderBy: id, orderDirection: asc) { id token0 { id } token1 { id } '
         'feeTier txCount totalValueLockedUSD } }')
    while True:
        try:
            r = requests.post(url, json={'query': q, 'variables': {'last': last_id}},
                              headers=_HEADERS, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning('subgraph page %d failed (%s); retrying', page, e)
            time.sleep(2); continue
        if data.get('errors'):
            log.warning('subgraph errors on page %d: %s', page, data['errors'][:1])
            break
        batch = data.get('data', {}).get('pools', [])
        if not batch:
            break
        for p in batch:
            try:
                fee_tier = int(p['feeTier'])
            except (TypeError, ValueError):
                continue
            pools.append({
                'id': p['id'].lower(),
                't0': p['token0']['id'].lower(),
                't1': p['token1']['id'].lower(),
                'fee': fee_tier // 100,                 # hundredths-of-bip → bps
                'tvl': float(p.get('totalValueLockedUSD') or 0.0),
                'tx_count': int(p.get('txCount') or 0),
            })
        last_id = batch[-1]['id']
        page += 1
        if len(batch) < 1000:
            break
    return pools


def build_pool_indices(pools):
    """by_addr: lowercase pool id -> pool;  by_pair: frozenset({t0,t1}) -> [pools]."""
    by_addr = {p['id']: p for p in pools}
    by_pair = defaultdict(list)
    for p in pools:
        by_pair[frozenset((p['t0'], p['t1']))].append(p)
    return by_addr, by_pair


# --------------------------------------------------------------------------- on-chain
_rpc_idx = defaultdict(int)

def _eth_code_exists(net: str, addr: str, attempts: int = 8):
    rpcs = PUBLIC_RPCS.get(net)
    if not rpcs or not addr:
        return None
    for _ in range(attempts):
        url = rpcs[_rpc_idx[net] % len(rpcs)]; _rpc_idx[net] += 1
        try:
            r = requests.post(url, json={'jsonrpc': '2.0', 'id': 1,
                                'method': 'eth_getCode', 'params': [addr, 'latest']},
                              headers=_HEADERS, timeout=20)
            j = r.json()
            if isinstance(j.get('result'), str):
                return len(j['result']) > 2
        except Exception:
            pass
        time.sleep(0.3)
    return None


def derive_v3(t0: str, t1: str, fee_bps: int, factory: str, init_hash: str):
    """CREATE2 V3 address; tokens sorted ascending, fee in hundredths-of-bip."""
    from eth_hash.auto import keccak
    t = sorted([bytes.fromhex(t0[2:]), bytes.fromhex(t1[2:])])
    salt = keccak(b'\x00' * 12 + t[0] + b'\x00' * 12 + t[1] + (fee_bps * 100).to_bytes(32, 'big'))
    return '0x' + keccak(b'\xff' + bytes.fromhex(factory[2:]) + salt + bytes.fromhex(init_hash[2:]))[12:].hex()


def load_dex_params():
    """Load factory/init_hash per protocol/network from config/dex-config.yaml."""
    import yaml
    for path in (os.path.join(REPO_ROOT, 'config', 'dex-config.yaml'),
                 '/app/config/dex-config.yaml', '/opt/airflow/config/dex-config.yaml'):
        try:
            with open(path) as fh:
                return yaml.safe_load(fh)
        except OSError:
            continue
    return {}


def net_config_key(net: str):
    n = (net or '').lower()
    return 'bsc' if n in ('bnb', 'bsc', 'binance') else ('ethereum' if n in ('ethereum', 'eth') else n)


def get_factory_init_hash(cfg, proto, net):
    pk = (proto or '').lower().replace(' ', '_').replace('-', '_')
    section = cfg.get(pk)
    if not isinstance(section, dict):
        return None
    entry = section if 'factory' in section else section.get(net_config_key(net))
    if not isinstance(entry, dict):
        return None
    return entry.get('factory'), entry.get('init_hash')


def _eth_liquidity(net, addr):
    """uint128 liquidity() of a V3 pool (selector 0x1a686502). Returns int or None."""
    rpcs = PUBLIC_RPCS.get(net)
    if not rpcs:
        return None
    for _ in range(8):
        url = rpcs[_rpc_idx[net] % len(rpcs)]; _rpc_idx[net] += 1
        try:
            r = requests.post(url, json={'jsonrpc': '2.0', 'id': 1,
                                'method': 'eth_call',
                                'params': [{'to': addr, 'data': '0x1a686502'}, 'latest']},
                              headers=_HEADERS, timeout=20)
            j = r.json()
            res = j.get('result')
            if isinstance(res, str) and res.startswith('0x') and len(res) >= 66:
                return int(res, 16)
        except Exception:
            pass
        time.sleep(0.3)
    return None


def onchain_resolve(net, proto, t0, t1, row_tvl, stored_fee=None):
    """Probe standard fee tiers via CREATE2 + eth_getCode and return
    (resolved_fee_bps, derived_address) or None.

    Disambiguation when several tiers exist:
      - if the row's stored fee is among them, keep it (fee was right, only the
        stored address was fabricated);
      - otherwise pick the tier whose on-chain liquidity() best matches the
        row's historical avg_tvl (or, if the row has no TVL, the highest
        liquidity).  This is authoritative without a subgraph.
    """
    cfg = load_dex_params()
    params = get_factory_init_hash(cfg, proto, net)
    if not params:
        return None
    factory, init_hash = params
    tiers = V3_TIERS_BY_PROTOCOL.get(proto)
    if not tiers:
        return None
    hits = []
    for fee in tiers:
        addr = derive_v3(t0, t1, fee, factory, init_hash)
        if _eth_code_exists(net, addr) is True:
            hits.append((fee, addr))
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    # several tiers exist
    if stored_fee is not None:
        for fee, addr in hits:
            if fee == int(stored_fee):
                return (fee, addr)           # stored fee was correct
    # disambiguate by on-chain liquidity vs row avg_tvl
    target = float(row_tvl or 0.0)
    best, best_liq = None, None
    for fee, addr in hits:
        liq = _eth_liquidity(net, addr)
        if liq is None:
            continue
        if best is None:
            best, best_liq = (fee, addr), liq
        elif target > 0 and abs(liq - target) < abs(best_liq - target):
            best, best_liq = (fee, addr), liq
        elif target == 0 and liq > best_liq:
            best, best_liq = (fee, addr), liq
    return best if best is not None else hits[0]


# --------------------------------------------------------------------------- matching
def classify_rows(rows, by_addr, by_pair, net, proto, existing_keys, subgraph_url=None):
    """Return (changes, stats).  changes = (cid, old_fee, new_fee, new_addr, reason, collides).
    existing_keys is the set of pre-existing canonical keys (chain_id, protocol_id,
    pool_name, fee_bps, pool_id) used to detect unique-constraint collisions.
    subgraph_url, when given, lets FEE_AMBIG rows be resolved authoritatively by
    finding which candidate pool actually had swaps in the row's active window."""
    changes, stats = [], defaultdict(int)
    for cid, fee_bps, stored, t0, t1, avg_tvl, d_lo, d_hi, chain_id, proto_id, pool_name in rows:
        if not t0 or not t1:
            stats['NO_TOKEN_ADDR'] += 1; continue
        pair = frozenset((t0.lower(), t1.lower()))
        sg = by_pair.get(pair, [])

        new_fee, new_addr, reason = None, None, None

        # 1. authoritative: stored address is a real subgraph pool
        if stored and stored.lower() in by_addr:
            p = by_addr[stored.lower()]
            new_fee, new_addr = p['fee'], p['id']
            reason = 'ADDR_MATCH'
        elif not sg:
            stats['NO_SUBGRAPH_POOL'] += 1; continue
        else:
            sg_tiers = {p['fee'] for p in sg}
            if fee_bps in sg_tiers:
                stats['FEE_OK'] += 1; continue          # fee already correct
            if len(sg) == 1:
                new_fee, new_addr = sg[0]['fee'], sg[0]['id']; reason = 'FEE_FIX_ONE'
            else:
                # 4. ambiguous: prefer authoritative swap-window lookup
                cand = [p['id'] for p in sg]
                picked = resolve_pool_by_swaps(subgraph_url, d_lo, d_hi, cand) \
                    if subgraph_url else None
                if picked and picked in by_addr:
                    new_fee, new_addr = by_addr[picked]['fee'], picked; reason = 'FEE_AMBIG_SWAP'
                else:
                    # fall back to TVL/txCount heuristic; only commit when confident
                    target = float(avg_tvl or 0.0)
                    if target > 0:
                        s = sorted(sg, key=lambda p: abs(p['tvl'] - target))
                        second = s[1] if len(s) > 1 else None
                        confident = second is None or \
                            abs(second['tvl'] - target) >= max(2 * abs(s[0]['tvl'] - target), 1)
                    else:
                        s = sorted(sg, key=lambda p: -p['tx_count'])
                        second = s[1] if len(s) > 1 else None
                        confident = s[0]['tx_count'] > 0 and \
                            (second is None or s[0]['tx_count'] >= 2 * (second['tx_count'] or 0))
                    if not confident:
                        stats['FEE_AMBIG_SKIP'] += 1; continue
                    new_fee, new_addr = s[0]['fee'], s[0]['id']; reason = 'FEE_AMBIG_HEUR'

        if new_fee is None:
            continue
        if new_fee == fee_bps and (not new_addr or (stored or '').lower() == new_addr):
            stats[reason + '_ok'] += 1; continue
        collides = (chain_id, proto_id, pool_name, new_fee, new_addr or '') in existing_keys \
            and (chain_id, proto_id, pool_name, fee_bps, stored or '') != \
                (chain_id, proto_id, pool_name, new_fee, new_addr or '')
        changes.append((cid, fee_bps, new_fee, new_addr, reason, collides))
        stats[reason + ('_collide' if collides else '_fix')] += 1
    return changes, stats


# --------------------------------------------------------------------------- main
def fetch_rows(net, proto):
    conn = psycopg2.connect(DATA_WAREHOUSE_DB); cur = conn.cursor()
    cur.execute("""
        SELECT lp.id, lp.fee_bps, LOWER(COALESCE(lp.pool_address,'')),
               cc0.contract_address, cc1.contract_address,
               COALESCE((SELECT AVG(ABS(h.tvl_usd)) FROM liquidity_pool_daily_stats h
                         WHERE h.pool_id=lp.id), 0),
               (SELECT MIN(h.day) FROM liquidity_pool_daily_stats h WHERE h.pool_id=lp.id),
               (SELECT MAX(h.day) FROM liquidity_pool_daily_stats h WHERE h.pool_id=lp.id),
               lp.chain_id, lp.protocol_id, lp.pool_name
        FROM liquidity_pool lp
        JOIN chain ch ON lp.chain_id=ch.id
        JOIN protocol pr ON lp.protocol_id=pr.id
        LEFT JOIN coin_contract cc0 ON cc0.coin_id=lp.coin0_id AND cc0.chain_id=lp.chain_id
        LEFT JOIN coin_contract cc1 ON cc1.coin_id=lp.coin1_id AND cc1.chain_id=lp.chain_id
        WHERE ch.name=%s AND pr.name=%s
    """, (net, proto))
    rows = cur.fetchall()

    # snapshot of all existing canonical keys for collision detection
    cur.execute("""
        SELECT chain_id, protocol_id, pool_name, fee_bps, COALESCE(pool_id,'')
        FROM liquidity_pool
    """)
    existing = set(cur.fetchall())
    conn.close()
    return rows, existing


def apply_changes(changes):
    """Apply changes defensively.  Several junk rows can resolve to the same
    real pool (duplicate targets); only the first per target key is applied and
    the rest are skipped (they need a separate merge/dedup).  Each update runs
    under a SAVEPOINT so a residual unique-key violation skips just that row
    instead of aborting the whole batch."""
    conn = psycopg2.connect(DATA_WAREHOUSE_DB); cur = conn.cursor()
    seen_pools, applied, skipped_dup, skipped_viol = set(), 0, 0, 0
    try:
        for cid, old, new, new_addr, reason, _c in changes:
            # dedup only on real pool identity: subgraph-style changes carry
            # new_addr (the real pool), so multiple junk rows that resolve to
            # the same pool collapse to one apply.  Onchain fee-only changes
            # leave new_addr=None and are each independent.
            if new_addr:
                if new_addr in seen_pools:
                    skipped_dup += 1; continue
                seen_pools.add(new_addr)
            cur.execute("SAVEPOINT s")
            try:
                cur.execute("""
                    UPDATE liquidity_pool
                       SET fee_bps=%s, pool_address=%s, pool_id=%s
                     WHERE id=%s AND (fee_bps IS DISTINCT FROM %s OR pool_address IS DISTINCT FROM %s)
                """, (new, new_addr, new_addr, cid, new, new_addr))
                cur.execute("RELEASE SAVEPOINT s")
                applied += 1
            except psycopg2.errors.UniqueViolation:
                cur.execute("ROLLBACK TO SAVEPOINT s")
                skipped_viol += 1
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT s")
                raise
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
    log.info('APPLIED %d updates (skipped %d duplicate-target, %d violation)',
             applied, skipped_dup, skipped_viol)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='Write changes (default: dry-run)')
    ap.add_argument('--network', default=None)
    ap.add_argument('--protocol', default=None)
    args = ap.parse_args()

    nets_protos = []
    for net in ('Ethereum', 'Arbitrum', 'Base', 'BNB'):
        for proto in ('Uniswap V3', 'PancakeSwap V3'):
            if args.network and net != args.network:
                continue
            if args.protocol and proto != args.protocol:
                continue
            nets_protos.append((net, proto))

    grand_changes, grand_stats = [], defaultdict(int)
    for net, proto in nets_protos:
        rows, existing = fetch_rows(net, proto)
        if not rows:
            continue
        log.info('%s / %s: %d rows', net, proto, len(rows))
        if net in UNISWAP_V3_SUBGRAPHS and proto == 'Uniswap V3':
            sub = UNISWAP_V3_SUBGRAPHS[net]
            subgraph_url = GATEWAY.format(key=os.getenv('GRAPH_API_KEY', ''), sub=sub)
            pools = fetch_subgraph_pools(sub)
            by_addr, by_pair = build_pool_indices(pools)
            log.info('  subgraph: %d pools indexed', len(pools))
            changes, stats = classify_rows(rows, by_addr, by_pair, net, proto,
                                           existing, subgraph_url=subgraph_url)
        else:
            # on-chain CREATE2 fallback (BNB, Base, or any PancakeSwap V3).
            # Resolves (fee, derived_address) per unique pair.
            changes, stats = [], defaultdict(int)
            seen_pairs = {}
            for cid, fee_bps, stored, t0, t1, avg_tvl, d_lo, d_hi, chain_id, proto_id, pool_name in rows:
                if not t0 or not t1:
                    stats['NO_TOKEN_ADDR'] += 1; continue
                key = frozenset((t0.lower(), t1.lower()))
                if key not in seen_pairs:
                    seen_pairs[key] = onchain_resolve(net, proto, t0, t1, avg_tvl, stored_fee=fee_bps)
                resolved = seen_pairs[key]
                if resolved is None:
                    stats['NO_ONCHAIN_POOL'] += 1; continue
                new_fee, new_addr = resolved
                if new_fee == fee_bps and (stored or '').lower() == new_addr.lower():
                    stats['ONCHAIN_ok'] += 1; continue
                collides = (chain_id, proto_id, pool_name, new_fee, new_addr.lower()) in existing \
                    and (chain_id, proto_id, pool_name, fee_bps, stored or '') != \
                        (chain_id, proto_id, pool_name, new_fee, new_addr.lower())
                changes.append((cid, fee_bps, new_fee, new_addr.lower(), 'ONCHAIN', collides))
                stats['ONCHAIN' + ('_collide' if collides else '_fix')] += 1
        for k, v in stats.items():
            grand_stats[k] += v
            log.info('    %-18s %d', k, v)
        grand_changes.extend(changes)

    log.info('---- totals ----')
    for k, v in sorted(grand_stats.items()):
        log.info('  %-18s %d', k, v)
    safe = [c for c in grand_changes if not c[5]]
    blocked = [c for c in grand_changes if c[5]]
    log.info('  CHANGES: %d safe, %d colliding (skipped), %d total',
             len(safe), len(blocked), len(grand_changes))

    if args.apply and safe:
        apply_changes(safe)
    elif grand_changes:
        log.info('dry-run; re-run with --apply to write. sample safe changes:')
        for c in safe[:15]:
            log.info('  cid=%-7s %s->%s  %s  [%s]', c[0], c[1], c[2], c[3] or '-', c[4])
        if blocked:
            log.info('  colliding (would need merge, skipped):')
            for c in blocked[:10]:
                log.info('  cid=%-7s %s->%s  [%s]', c[0], c[1], c[2], c[4])
    else:
        log.info('nothing to change')


if __name__ == '__main__':
    main()
