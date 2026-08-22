"""Route topology classifier for swap legs.

Decomposes a transaction's swap logs into maximal contiguous chains and
registers each chain as (origin_destination_pair, route, route_hop) triples in
the warehouse, then attributes every swap log to its route via swaps.route_id.

The chain/route derivation is pure (SQL-free) so it can be unit tested and, on
re-classification, converges to the same route_id (idempotent canonical-key
upserts). DB helpers are thin psycopg2 wrappers that accept an open cursor so
callers decide transaction boundaries (ingest sweep vs. backfill vs. rollup).
"""

from __future__ import annotations

import time
import logging
import csv
import io
import hashlib
from collections import defaultdict
from typing import Dict, List, Optional
import psycopg2.extras

try:
    from include.settings import load_distribution_config
except ImportError:
    from settings import load_distribution_config

log = logging.getLogger(__name__)

LEG_AMOUNT_GATE = "(amount_usd >= 10.0 OR amount_usd = 0.0 OR amount_usd IS NULL)"
ROUTE_WRITE_LOCK = "chaintelligence.route-dimension-write"

# Canonical short-lived raw store for the switchover. Rollups and the
# classification queue read from this instead of the legacy `swaps` table.
import os as _os
RAW_SWAP_TABLE = _os.getenv('SWAP_RAW_TABLE', 'swaps_staging').strip()


# ---------------------------------------------------------------------------
# Pure topology derivation (unit-testable, SQL-free)
# ---------------------------------------------------------------------------

def _input_flow(leg: Dict) -> tuple:
    """Return (input_token, output_token) for a swap leg.

    The input side of a pool swap log is the token whose amount is positive
    (amount > 0 == spent token), matching the RouteAnalyzer convention.
    """
    if leg.get('amount0', 0) > 0:
        return leg['token0'], leg['token1']
    return leg['token1'], leg['token0']


def chains_in_tx(legs: List[Dict]) -> List[Dict]:
    """Split a transaction's legs (sorted by log_index) into maximal contiguous
    chains. Each returned chain has keys:

      tokens: [addr0, addr1, ..., addrN]  (origin ... destination)
      pools : [pool_id x N]               (hop i connects tokens[i] -> tokens[i+1])
      legs  : the leg dicts in that chain (log order)

    Disjoint swaps (e.g. WETH->USDC + AAVE->USDT in one tx) split into separate
    chains; a round-trip ETH->USDC->ETH stays one chain (origin == dest).
    """
    ordered = sorted(legs, key=lambda l: l.get('log_index', 0))
    chains: List[Dict] = []
    current: Optional[Dict] = None

    for leg in ordered:
        in_tok, out_tok = _input_flow(leg)
        if current is None:
            current = {'tokens': [in_tok], 'pools': [], 'legs': []}
        elif in_tok != current['tokens'][-1]:
            chains.append(current)
            current = {'tokens': [in_tok], 'pools': [], 'legs': []}
        current['tokens'].append(out_tok)
        current['pools'].append(leg['pool_id'])
        current['legs'].append(leg)

    if current is not None:
        chains.append(current)
    return chains


def compute_pair_id(chain_id: int, origin: str, dest: str) -> int:
    """Compute deterministic signed 64-bit integer ID for an origin-destination pair."""
    key = f"{chain_id}:{(origin or '').lower()}:{(dest or '').lower()}".encode('utf-8')
    return int.from_bytes(hashlib.md5(key).digest()[:8], 'big', signed=True)


def compute_route_id(pair_id: int, pools: List[int]) -> int:
    """Compute deterministic signed 64-bit integer ID for a route."""
    pools_str = ":".join(str(p) for p in pools)
    key = f"{pair_id}:{pools_str}".encode('utf-8')
    return int.from_bytes(hashlib.md5(key).digest()[:8], 'big', signed=True)


def canonical_key(pair_id: int, pools: List[int]) -> str:
    """Deterministic identity for a route: pair_id joined with ordered pool ids."""
    return f"{pair_id}:" + ":".join(str(p) for p in pools)


def route_volume(chain: Dict, origin_contract: str) -> float:
    """USD value of the user's input for a chain: sum amount_usd over the legs
    whose input token is the chain origin. A single-leg chain always counts it."""
    total = 0.0
    for leg in chain['legs']:
        in_tok, _ = _input_flow(leg)
        if len(chain['legs']) == 1 or in_tok == origin_contract:
            v = leg.get('amount_usd')
            if v is not None:
                total += float(v)
    return total


# ---------------------------------------------------------------------------
# DB helpers (operate on the caller's cursor)
# ---------------------------------------------------------------------------

def load_classifier_caches(cur) -> tuple[Dict, Dict]:
    """Pre-load existing origin_destination_pairs and routes into in-memory dicts."""
    pair_cache: Dict = {}
    route_cache: Dict = {}

    cur.execute("""
        SELECT chain_id, lower(origin_contract), lower(dest_contract), id
        FROM origin_destination_pair
    """)
    for chain_id, origin, dest, pair_id in cur.fetchall():
        pair_cache[(chain_id, origin, dest)] = pair_id

    cur.execute("""
        SELECT canonical_key, route_id
        FROM route
    """)
    for key, route_id in cur.fetchall():
        route_cache[key] = route_id

    log.info("Pre-loaded %d pairs and %d routes into classifier cache", len(pair_cache), len(route_cache))
    return pair_cache, route_cache


def _token_meta(legs: List[Dict]) -> Dict[str, Dict]:
    """Build contract-address -> {coin_id, symbol} from the legs' pool tokens."""
    meta: Dict[str, Dict] = {}
    for leg in legs:
        for key in ('token0', 'token1'):
            addr = (leg.get(key) or '').lower()
            if not addr:
                continue
            target = meta.setdefault(addr, {'coin_id': None, 'symbol': None})
            cid = leg.get('coin0_id' if key == 'token0' else 'coin1_id')
            sym = leg.get('symbol0' if key == 'token0' else 'symbol1')
            if cid is not None:
                target['coin_id'] = cid
            if sym:
                target['symbol'] = sym
    return meta


def resolve_pair(cur, chain_id: int, origin: str, dest: str,
                 ometa: Optional[Dict], dmeta: Optional[Dict], ts,
                 pair_cache: Optional[Dict] = None) -> int:
    """Find-or-create an origin_destination_pair using deterministic 64-bit hash id."""
    origin_clean = (origin or '').lower()
    dest_clean = (dest or '').lower()
    cache_key = (chain_id, origin_clean, dest_clean)
    pair_id = compute_pair_id(chain_id, origin_clean, dest_clean)

    if pair_cache is not None and cache_key in pair_cache:
        return pair_cache[cache_key]

    for attempt in range(10):
        try:
            cur.execute("SAVEPOINT pair_sp")
            cur.execute(
                """
                INSERT INTO origin_destination_pair (
                    id, chain_id, origin_contract, dest_contract,
                    origin_coin_id, dest_coin_id, origin_symbol, dest_symbol,
                    first_seen, last_seen
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chain_id, origin_contract, dest_contract)
                DO UPDATE SET
                    origin_coin_id = COALESCE(origin_destination_pair.origin_coin_id, EXCLUDED.origin_coin_id),
                    dest_coin_id   = COALESCE(origin_destination_pair.dest_coin_id, EXCLUDED.dest_coin_id),
                    origin_symbol  = COALESCE(origin_destination_pair.origin_symbol, EXCLUDED.origin_symbol),
                    dest_symbol    = COALESCE(origin_destination_pair.dest_symbol, EXCLUDED.dest_symbol),
                    first_seen     = LEAST(origin_destination_pair.first_seen, EXCLUDED.first_seen),
                    last_seen      = GREATEST(origin_destination_pair.last_seen, EXCLUDED.last_seen)
                """,
                (pair_id, chain_id, origin_clean, dest_clean,
                 (ometa or {}).get('coin_id'), (dmeta or {}).get('coin_id'),
                 (ometa or {}).get('symbol'), (dmeta or {}).get('symbol'),
                 ts, ts),
            )
            cur.execute("RELEASE SAVEPOINT pair_sp")
            if pair_cache is not None:
                pair_cache[cache_key] = pair_id
            return pair_id
        except Exception:
            try:
                cur.execute("ROLLBACK TO SAVEPOINT pair_sp")
            except Exception:
                pass
            if attempt == 9:
                raise
            time.sleep(0.2 * (1.5 ** attempt))


def resolve_route(cur, pair_id: int, chain_id: int, pools: List[int],
                  tokens: List[str], ts,
                  route_cache: Optional[Dict] = None) -> int:
    """Find-or-create a route using deterministic 64-bit hash route_id."""
    route_key = canonical_key(pair_id, pools)
    route_id = compute_route_id(pair_id, pools)

    if route_cache is not None and route_key in route_cache:
        return route_cache[route_key]

    for attempt in range(10):
        try:
            cur.execute("SAVEPOINT route_sp")
            cur.execute(
                """
                INSERT INTO route (route_id, pair_id, chain_id, hops, canonical_key, first_seen, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (canonical_key)
                DO UPDATE SET
                    first_seen = LEAST(route.first_seen, EXCLUDED.first_seen),
                    last_seen  = GREATEST(route.last_seen, EXCLUDED.last_seen)
                """,
                (route_id, pair_id, chain_id, len(pools), route_key, ts, ts),
            )
            for idx, pool_id in enumerate(pools):
                token_in = (tokens[idx] or '').lower()
                token_out = (tokens[idx + 1] or '').lower()
                cur.execute(
                    """
                    INSERT INTO route_hop (route_id, seq, pool_id, token_in, token_out)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (route_id, seq) DO NOTHING
                    """,
                    (route_id, idx, pool_id, token_in, token_out),
                )
            cur.execute("RELEASE SAVEPOINT route_sp")
            if route_cache is not None:
                route_cache[route_key] = route_id
            return route_id
        except Exception:
            try:
                cur.execute("ROLLBACK TO SAVEPOINT route_sp")
            except Exception:
                pass
            if attempt == 9:
                raise
            time.sleep(0.2 * (1.5 ** attempt))


def _legs_for_txs(cur, tx_hashes: List[str], table_name: str = 'swaps') -> List[Dict]:
    """Fetch classifier legs (with per-token addresses/symbols via pool joins)
    for a set of tx hashes, ordered by (tx_hash, log_index)."""
    target_table = table_name.strip() if table_name and table_name.strip() else 'swaps'
    cur.execute(
        f"""
        SELECT
            s.tx_hash, s.log_index, s.ts, s.pool_id,
            s.amount0, s.amount1, s.amount_usd,
            lp.chain_id,
            cc0.contract_address AS token0, cc1.contract_address AS token1,
            c0.symbol AS symbol0, c1.symbol AS symbol1,
            lp.coin0_id, lp.coin1_id
        FROM {target_table} s
        JOIN liquidity_pool lp ON s.pool_id = lp.id
        JOIN coin_contract cc0 ON cc0.coin_id = lp.coin0_id AND cc0.chain_id = lp.chain_id
        JOIN coin_contract cc1 ON cc1.coin_id = lp.coin1_id AND cc1.chain_id = lp.chain_id
        JOIN coin c0 ON c0.coin_id = lp.coin0_id
        JOIN coin c1 ON c1.coin_id = lp.coin1_id
        WHERE s.tx_hash IN %s
        ORDER BY s.tx_hash, s.log_index
        """,
        (tuple(tx_hashes),),
    )
    legs: List[Dict] = []
    for (tx_hash, log_index, ts, pool_id, amount0, amount1, amount_usd,
         chain_id, token0, token1, s0, s1, coin0_id, coin1_id) in cur.fetchall():
        legs.append({
            'tx_hash': tx_hash, 'log_index': log_index, 'ts': ts,
            'pool_id': pool_id, 'amount0': amount0, 'amount1': amount1,
            'amount_usd': amount_usd, 'chain_id': chain_id, 'token0': token0,
            'token1': token1, 'symbol0': s0, 'symbol1': s1,
            'coin0_id': coin0_id, 'coin1_id': coin1_id,
        })
    return legs


def collect_route_staging(cur, tx_hashes: List[str], table_name: str = 'swaps') -> tuple[list[dict], list[dict], set[str]]:
    """Read and reconstruct routes without writing route dimensions.

    The backfill uses this read-only phase in parallel workers. The returned
    natural route keys are independent of database-generated pair/route IDs,
    which lets the parent merge all candidates through set-based SQL.
    """
    legs = _legs_for_txs(cur, tx_hashes, table_name=table_name)
    by_tx: Dict[str, List[Dict]] = defaultdict(list)
    candidates: Dict[str, dict] = {}
    assignments: list[dict] = []
    affected_days: set[str] = set()

    for leg in legs:
        by_tx[leg['tx_hash']].append(leg)
        ts = leg.get('ts')
        if ts:
            affected_days.add(ts[:10] if isinstance(ts, str) else ts.strftime('%Y-%m-%d'))

    for tx_legs in by_tx.values():
        for chain in chains_in_tx(tx_legs):
            meta = _token_meta(chain['legs'])
            origin = (chain['tokens'][0] or '').lower()
            dest = (chain['tokens'][-1] or '').lower()
            chain_id = chain['legs'][0]['chain_id']
            ts = chain['legs'][0]['ts']
            pools = [int(pool_id) for pool_id in chain['pools']]
            # Unit separator avoids ambiguity between addresses and pool IDs.
            natural_key = f"{chain_id}\x1f{origin}\x1f{dest}\x1f{','.join(map(str, pools))}"
            candidate = candidates.setdefault(natural_key, {
                'candidate_key': natural_key,
                'chain_id': chain_id,
                'origin_contract': origin,
                'dest_contract': dest,
                'origin_coin_id': meta.get(origin, {}).get('coin_id'),
                'dest_coin_id': meta.get(dest, {}).get('coin_id'),
                'origin_symbol': meta.get(origin, {}).get('symbol'),
                'dest_symbol': meta.get(dest, {}).get('symbol'),
                'pools': pools,
                'tokens': [(token or '').lower() for token in chain['tokens']],
                'first_seen': ts,
                'last_seen': ts,
            })
            if ts and candidate['first_seen'] and ts < candidate['first_seen']:
                candidate['first_seen'] = ts
            if ts and candidate['last_seen'] and ts > candidate['last_seen']:
                candidate['last_seen'] = ts
            for leg in chain['legs']:
                assignments.append({
                    'candidate_key': natural_key,
                    'tx_hash': leg['tx_hash'],
                    'log_index': leg['log_index'],
                    'ts': leg['ts'],
                })

    return list(candidates.values()), assignments, affected_days


def merge_route_staging(cur, candidates: list[dict], assignments: list[dict], table_name: str = 'swaps') -> int:
    """Merge staged route topology and assignments using set-based SQL."""
    if not candidates or not assignments:
        return 0
    # Coordinate with the live ingestion classifier. A transaction-level
    # advisory lock makes route dimension writes wait instead of deadlocking.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ROUTE_WRITE_LOCK,))
    target_table = table_name.strip() if table_name and table_name.strip() else 'swaps'
    candidate_rows = []
    for c in candidates:
        pid = compute_pair_id(c['chain_id'], c['origin_contract'], c['dest_contract'])
        rid = compute_route_id(pid, c['pools'])
        c['pair_id'] = pid
        c['route_id'] = rid
        candidate_rows.append((
            c['candidate_key'], c['chain_id'], c['origin_contract'], c['dest_contract'],
            c['origin_coin_id'], c['dest_coin_id'], c['origin_symbol'], c['dest_symbol'],
            c['pools'], c['tokens'], c['first_seen'], c['last_seen'],
            pid, rid
        ))

    cur.execute("""
        CREATE TEMP TABLE route_stage_candidates (
            candidate_key TEXT PRIMARY KEY,
            chain_id SMALLINT NOT NULL,
            origin_contract VARCHAR(64) NOT NULL,
            dest_contract VARCHAR(64) NOT NULL,
            origin_coin_id INTEGER,
            dest_coin_id INTEGER,
            origin_symbol VARCHAR(10),
            dest_symbol VARCHAR(10),
            pools BIGINT[] NOT NULL,
            tokens TEXT[] NOT NULL,
            first_seen TIMESTAMPTZ,
            last_seen TIMESTAMPTZ,
            pair_id BIGINT NOT NULL,
            route_id BIGINT NOT NULL
        ) ON COMMIT DROP
    """)
    cur.execute("""
        CREATE TEMP TABLE route_stage_assignments (
            candidate_key TEXT NOT NULL,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            ts TIMESTAMPTZ NOT NULL
        ) ON COMMIT DROP
    """)
    def copy_rows(sql: str, rows: list[tuple]):
        """COPY rows into a temporary table without building SQL statements."""
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator='\n')
        for row in rows:
            encoded = []
            for value in row:
                if value is None:
                    encoded.append('\\N')
                elif isinstance(value, list):
                    array_items = []
                    for item in value:
                        item_str = str(item).replace('\\', '\\\\').replace('"', '\\"')
                        array_items.append(f'"{item_str}"')
                    encoded.append('{' + ','.join(array_items) + '}')
                else:
                    encoded.append(str(value))
            writer.writerow(encoded)
        stream.seek(0)
        cur.copy_expert(sql, stream)

    assignment_rows = [(a['candidate_key'], a['tx_hash'], a['log_index'], a['ts']) for a in assignments]
    copy_rows("""
        COPY route_stage_candidates
        (candidate_key, chain_id, origin_contract, dest_contract, origin_coin_id,
         dest_coin_id, origin_symbol, dest_symbol, pools, tokens, first_seen, last_seen,
         pair_id, route_id)
        FROM STDIN WITH (FORMAT csv, NULL '\\N')
    """, candidate_rows)
    copy_rows("""
        COPY route_stage_assignments (candidate_key, tx_hash, log_index, ts)
        FROM STDIN WITH (FORMAT csv, NULL '\\N')
    """, assignment_rows)

    cur.execute("""
        INSERT INTO origin_destination_pair
        (id, chain_id, origin_contract, dest_contract, origin_coin_id, dest_coin_id,
         origin_symbol, dest_symbol, first_seen, last_seen)
        SELECT pair_id, chain_id, origin_contract, dest_contract, MAX(origin_coin_id), MAX(dest_coin_id),
               MAX(origin_symbol), MAX(dest_symbol), MIN(first_seen), MAX(last_seen)
        FROM route_stage_candidates
        GROUP BY pair_id, chain_id, origin_contract, dest_contract
        ON CONFLICT (chain_id, origin_contract, dest_contract) DO UPDATE SET
            origin_coin_id = COALESCE(origin_destination_pair.origin_coin_id, EXCLUDED.origin_coin_id),
            dest_coin_id = COALESCE(origin_destination_pair.dest_coin_id, EXCLUDED.dest_coin_id),
            origin_symbol = COALESCE(origin_destination_pair.origin_symbol, EXCLUDED.origin_symbol),
            dest_symbol = COALESCE(origin_destination_pair.dest_symbol, EXCLUDED.dest_symbol),
            first_seen = LEAST(origin_destination_pair.first_seen, EXCLUDED.first_seen),
            last_seen = GREATEST(origin_destination_pair.last_seen, EXCLUDED.last_seen)
    """)
    cur.execute("""
        INSERT INTO route (route_id, pair_id, chain_id, hops, canonical_key, first_seen, last_seen)
        SELECT route_id, pair_id, chain_id, cardinality(pools),
               pair_id::text || ':' || array_to_string(pools, ':'),
               MIN(first_seen), MAX(last_seen)
        FROM route_stage_candidates
        GROUP BY route_id, pair_id, chain_id, pools
        ON CONFLICT (canonical_key) DO UPDATE SET
            first_seen = LEAST(route.first_seen, EXCLUDED.first_seen),
            last_seen = GREATEST(route.last_seen, EXCLUDED.last_seen)
    """)
    cur.execute("""
        INSERT INTO route_hop (route_id, seq, pool_id, token_in, token_out)
        SELECT c.route_id, n - 1, c.pools[n], c.tokens[n], c.tokens[n + 1]
        FROM route_stage_candidates c
        CROSS JOIN LATERAL generate_subscripts(c.pools, 1) AS x(n)
        ON CONFLICT (route_id, seq) DO NOTHING
    """)
    cur.execute(f"""
        UPDATE {target_table} s
        SET route_id = c.route_id
        FROM route_stage_assignments a
        JOIN route_stage_candidates c ON c.candidate_key = a.candidate_key
        WHERE s.tx_hash = a.tx_hash
          AND s.log_index = a.log_index
          AND s.ts = a.ts
          AND s.route_id IS DISTINCT FROM c.route_id
    """)
    return cur.rowcount


def classify_legs(cur, legs: List[Dict], pair_cache: Optional[Dict] = None,
                  route_cache: Optional[Dict] = None, table_name: str = 'swaps') -> tuple:
    """Given fetched legs, register chains/pairs/routes and set swaps.route_id
    on every leg using bulk SQL updates.

    Returns ``(number_of_legs_attributed, set_of_affected_days, dirty)`` where
    ``dirty`` is ``{'route_day': {(route_id, day)}, 'pool_day': {(pool_id, day)}}``
    for incremental materializers (dirty_route_day / dirty_pool_day).
    """
    # Use the same transaction-level lock as the bulk backfill merge so live
    # ingestion and historical backfill serialize route dimension writes.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ROUTE_WRITE_LOCK,))
    by_tx: Dict[str, List[Dict]] = defaultdict(list)
    affected_days: set[str] = set()
    dirty_route_days: set = set()
    dirty_pool_days: set = set()

    def _day_str(ts):
        return ts[:10] if isinstance(ts, str) else ts.strftime('%Y-%m-%d')

    for leg in legs:
        by_tx[leg['tx_hash']].append(leg)
        ts = leg.get('ts')
        if ts:
            affected_days.add(_day_str(ts))

    update_rows = []
    for tx_hash, tx_legs in by_tx.items():
        for chain in chains_in_tx(tx_legs):
            meta = _token_meta(chain['legs'])
            origin = (chain['tokens'][0] or '').lower()
            dest = (chain['tokens'][-1] or '').lower()
            chain_id = chain['legs'][0]['chain_id']
            ts = chain['legs'][0]['ts']
            pair_id = resolve_pair(cur, chain_id, origin, dest,
                                   meta.get(origin), meta.get(dest), ts,
                                   pair_cache=pair_cache)
            route_id = resolve_route(cur, pair_id, chain_id,
                                     chain['pools'], chain['tokens'], ts,
                                     route_cache=route_cache)
            if route_id is not None:
                day = _day_str(ts)
                assert day is not None
                import datetime as _dt
                d = _dt.date.fromisoformat(day)
                for leg in chain['legs']:
                    update_rows.append((route_id, leg['tx_hash'], leg['log_index'], leg['ts']))
                    dirty_route_days.add((route_id, d))
                    dirty_pool_days.add((leg['pool_id'], d))

    if not update_rows:
        return 0, set(), {'route_days': set(), 'pool_days': set()}

    target_table = table_name.strip() if table_name and table_name.strip() else 'swaps'
    update_sql = f"""
        UPDATE {target_table} AS s
        SET route_id = v.route_id
        FROM (VALUES %s) AS v(route_id, tx_hash, log_index, ts)
        WHERE s.tx_hash = v.tx_hash
          AND s.log_index = v.log_index
          AND s.ts = v.ts
    """
    try:
        psycopg2.extras.execute_values(cur, update_sql, update_rows, page_size=1000)
    except psycopg2.errors.ForeignKeyViolation as fk_err:
        log.warning("ForeignKeyViolation in classify_legs: %s. Skipping this batch update.", fk_err)
        return 0, affected_days, {'route_days': set(), 'pool_days': set()}
    return len(update_rows), affected_days, {'route_days': set(dirty_route_days), 'pool_days': set(dirty_pool_days)}


def classify_tx_hashes(cur, tx_hashes: List[str], pair_cache: Optional[Dict] = None,
                       route_cache: Optional[Dict] = None, table_name: str = 'swaps') -> tuple[int, set[str]]:
    """Pull legs for a set of tx hashes and classify them. Returns (count, affected_days). Idempotent."""
    if not tx_hashes:
        return 0, set()
    legs = _legs_for_txs(cur, tx_hashes, table_name=table_name)
    if not legs:
        return 0, set(), {'route_days': set(), 'pool_days': set()}
    return classify_legs(cur, legs, pair_cache=pair_cache, route_cache=route_cache, table_name=table_name)


def recompute_daily_stats(cur, days: List[str], chunk_days: int = 7, table_name: str = None) -> int:
    """Recompute route_daily_stats for a set of ISO days ('YYYY-MM-DD').

    The table is derived (materialized), so DELETE+INSERT is correct regardless
    of classification order — late mixed-protocol legs reclassify a tx's route.
    Executes in fast chunked date ranges with integer coin_id matching and partition pruning.
    """
    if not days:
        return 0

    source_table = (table_name or RAW_SWAP_TABLE).strip()
    sorted_days = sorted(set(days))
    log.info("Recomputing route_daily_stats for %d days (from %s to %s)...",
             len(sorted_days), sorted_days[0], sorted_days[-1])

    from datetime import datetime, timedelta
    start_dt = datetime.strptime(sorted_days[0], '%Y-%m-%d').date()
    end_dt = datetime.strptime(sorted_days[-1], '%Y-%m-%d').date()

    total_rows = 0
    curr = start_dt
    chunk_idx = 1
    import time as _time
    while curr <= end_dt:
        chunk_end = min(curr + timedelta(days=chunk_days), end_dt + timedelta(days=1))
        t_start = f"{curr.isoformat()} 00:00:00"
        t_end = f"{chunk_end.isoformat()} 00:00:00"

        c_t0 = _time.time()
        cur.execute(
            "DELETE FROM route_daily_stats WHERE day >= %s AND day < %s",
            (curr.isoformat(), chunk_end.isoformat()),
        )

        cur.execute(
            f"""
            INSERT INTO route_daily_stats (route_id, day, tx_count, swap_count, volume_usd, fees_usd)
            SELECT
                s.route_id,
                s.ts::date AS day,
                count(DISTINCT s.tx_hash) AS tx_count,
                count(*) AS swap_count,
                sum(CASE
                    WHEN s.amount0 > 0 AND lp.coin0_id = p.origin_coin_id THEN s.amount_usd
                    WHEN s.amount1 > 0 AND lp.coin1_id = p.origin_coin_id THEN s.amount_usd
                    ELSE 0 END) AS volume_usd,
                sum(s.amount_usd * COALESCE(lp.fee_bps, 0) / 10000.0) AS fees_usd
            FROM {source_table} s
            JOIN liquidity_pool lp ON s.pool_id = lp.id
            JOIN route r ON s.route_id = r.route_id
            JOIN origin_destination_pair p ON r.pair_id = p.id
            WHERE s.route_id IS NOT NULL
              AND s.ts >= %s::timestamp AND s.ts < %s::timestamp
              AND ({LEG_AMOUNT_GATE})
            GROUP BY s.route_id, s.ts::date
            """,
            (t_start, t_end),
        )
        n = cur.rowcount
        c_elapsed = _time.time() - c_t0
        total_rows += n if n > 0 else 0
        log.info("  [daily_stats chunk %d: %s .. %s] inserted %d rows (%.2fs)",
                 chunk_idx, curr.isoformat(), (chunk_end - timedelta(days=1)).isoformat(), n if n > 0 else 0, c_elapsed)
        curr = chunk_end
        chunk_idx += 1

    log.info("Finished recomputing route_daily_stats (%d rows inserted across %d days).",
             total_rows, len(sorted_days))
    return total_rows


def recompute_distribution_buckets(cur, days: List[str], chunk_days: int = 7, table_name: str = None) -> int:
    """Rebuild swap-size buckets for EVERY route for the supplied days.

    A routed transaction contributes its first route leg once, matching the
    input-volume semantics used by route analysis. The bucket parameters
    (bucket_count, min/max amount USD) come from the global
    ``config/swap-distribution.yaml`` — there is no per-route config anymore.
    """
    cfg = load_distribution_config()
    return _recompute_distribution_buckets(
        cur, days, chunk_days, table_name,
        grain='route_id',
        bucket_table='route_daily_stats_bucket',
        bucket_count=cfg['bucket_count'],
        min_amount_usd=cfg['min_amount_usd'],
        max_amount_usd=cfg['max_amount_usd'],
    )


def recompute_pool_distribution_buckets(cur, days: List[str], chunk_days: int = 7, table_name: str = None) -> int:
    """Rebuild swap-size buckets for EVERY pool for the supplied days.

    Mirrors the route distribution buckets at the pool grain: a transaction
    contributes its first swap leg on a pool once, bucketed by log-volume.
    The bucket parameters come from the global ``config/swap-distribution.yaml``.
    """
    cfg = load_distribution_config()
    return _recompute_distribution_buckets(
        cur, days, chunk_days, table_name,
        grain='pool_id',
        bucket_table='liquidity_pool_daily_stats_bucket',
        bucket_count=cfg['bucket_count'],
        min_amount_usd=cfg['min_amount_usd'],
        max_amount_usd=cfg['max_amount_usd'],
    )


def _recompute_distribution_buckets(cur, days: List[str], chunk_days: int, table_name: str,
                                    grain: str, bucket_table: str,
                                    bucket_count: int, min_amount_usd: float, max_amount_usd: float) -> int:
    """Shared log-volume bucket rebuild used by the route and pool variants.

    Bucketing is unconditional: every route (or pool) with swap legs in the
    window is bucketed with the supplied global parameters.
    """
    if not days:
        return 0

    source_table = (table_name or RAW_SWAP_TABLE).strip()

    sorted_days = sorted(set(days))
    from datetime import datetime, timedelta
    start_dt = datetime.strptime(sorted_days[0], '%Y-%m-%d').date()
    end_dt = datetime.strptime(sorted_days[-1], '%Y-%m-%d').date()
    total_rows = 0
    curr = start_dt
    while curr <= end_dt:
        chunk_end = min(curr + timedelta(days=chunk_days), end_dt + timedelta(days=1))
        cur.execute(
            f"""
            DELETE FROM {bucket_table}
            WHERE day >= %s AND day < %s
            """,
            (curr.isoformat(), chunk_end.isoformat()),
        )
        cur.execute(
            f"""
            WITH first_legs AS (
                SELECT DISTINCT ON (s.{grain}, s.tx_hash, s.ts::date)
                    s.{grain},
                    s.tx_hash,
                    s.ts::date AS day,
                    s.amount_usd,
                    s.amount_usd * COALESCE(lp.fee_bps, 0) / 10000.0 AS fee_usd
                FROM {source_table} s
                JOIN liquidity_pool lp ON s.pool_id = lp.id
                WHERE s.ts >= %s::timestamp
                  AND s.ts < %s::timestamp
                  AND s.{grain} IS NOT NULL
                  AND s.amount_usd >= %s
                  AND s.amount_usd <= %s
                ORDER BY s.{grain}, s.tx_hash, s.ts::date, s.log_index
            ), bucketed AS (
                SELECT
                    {grain},
                    day,
                    LEAST(%s::int, width_bucket(
                        LN(amount_usd), LN(%s::float8), LN(%s::float8), %s::int
                    ))::smallint AS bucket_index,
                    tx_hash,
                    amount_usd,
                    fee_usd,
                    LN(amount_usd) AS log_amount
                FROM first_legs
            )
            INSERT INTO {bucket_table}
                ({grain}, day, bucket_index, tx_count, sample_count, volume_usd, fees_usd, log_sum, log_sum2)
            SELECT {grain}, day, bucket_index,
                   COUNT(DISTINCT tx_hash), COUNT(*), SUM(amount_usd), SUM(fee_usd),
                   SUM(log_amount), SUM(log_amount * log_amount)
            FROM bucketed
            WHERE bucket_index BETWEEN 1 AND 256
            GROUP BY {grain}, day, bucket_index
            ON CONFLICT ({grain}, day, bucket_index) DO UPDATE SET
                tx_count = EXCLUDED.tx_count,
                sample_count = EXCLUDED.sample_count,
                volume_usd = EXCLUDED.volume_usd,
                fees_usd = EXCLUDED.fees_usd,
                log_sum = EXCLUDED.log_sum,
                log_sum2 = EXCLUDED.log_sum2
            """,
            (curr.isoformat(), chunk_end.isoformat(),
             min_amount_usd, max_amount_usd,
             bucket_count, min_amount_usd, max_amount_usd, bucket_count),
        )
        total_rows += cur.rowcount if cur.rowcount > 0 else 0
        curr = chunk_end

    log.info("Rebuilt %d %s bucket rows for %d days (%s).",
             total_rows, grain, len(sorted_days), bucket_table)
    return total_rows


def seed_top_routes_and_attribute(cur, table_name: str = 'swaps', top_n_pairs: int = 500) -> tuple[int, set[str]]:
    """Top-down route ingestion phase.
    
    1. Finds top origin-destination pairs and pool sequences from candidate swaps.
    2. Idempotently registers origin_destination_pair, route, and route_hop using deterministic 64-bit hash IDs.
    3. Executes set-based bulk SQL UPDATE directly matching swaps to route_id.
    Returns (attributed_swaps_count, affected_days).
    """
    target_table = table_name.strip() if table_name and table_name.strip() else 'swaps'
    log.info("Starting top-down seed ingestion on %s (top %d pairs)...", target_table, top_n_pairs)

    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ROUTE_WRITE_LOCK,))

    # Step 1: Discover high-frequency (chain_id, origin_contract, dest_contract, pools) tuples
    cur.execute(f"""
        WITH pool_chains AS (
            SELECT
                s.tx_hash,
                s.ts,
                lp.chain_id,
                cc0.contract_address AS token0,
                cc1.contract_address AS token1,
                s.pool_id,
                s.log_index,
                c0.symbol AS symbol0,
                c1.symbol AS symbol1,
                lp.coin0_id,
                lp.coin1_id
            FROM {target_table} s
            JOIN liquidity_pool lp ON s.pool_id = lp.id
            JOIN coin_contract cc0 ON cc0.coin_id = lp.coin0_id AND cc0.chain_id = lp.chain_id
            JOIN coin_contract cc1 ON cc1.coin_id = lp.coin1_id AND cc1.chain_id = lp.chain_id
            JOIN coin c0 ON c0.coin_id = lp.coin0_id
            JOIN coin c1 ON c1.coin_id = lp.coin1_id
            WHERE s.route_id IS NULL
        ),
        tx_summary AS (
            SELECT
                tx_hash,
                min(ts) AS ts,
                min(chain_id) AS chain_id,
                (array_agg(CASE WHEN amount0_spent THEN token0 ELSE token1 END ORDER BY log_index))[1] AS origin_contract,
                (array_agg(CASE WHEN amount0_spent THEN token1 ELSE token0 END ORDER BY log_index))[cardinality(array_agg(log_index))] AS dest_contract,
                array_agg(pool_id ORDER BY log_index) AS pools,
                count(*) AS leg_count
            FROM (
                SELECT *, (token0 < token1) AS amount0_spent FROM pool_chains
            ) sub
            GROUP BY tx_hash
        )
        SELECT chain_id, origin_contract, dest_contract, pools, min(ts) AS first_seen, max(ts) AS last_seen, count(*) AS tx_freq
        FROM tx_summary
        WHERE origin_contract IS NOT NULL AND dest_contract IS NOT NULL
        GROUP BY chain_id, origin_contract, dest_contract, pools
        ORDER BY tx_freq DESC
        LIMIT %s
    """, (top_n_pairs,))

    rows = cur.fetchall()
    if not rows:
        log.info("Top-down seed: No candidate routes found to seed.")
        return 0, set()

    log.info("Discovered %d candidate top route seeds. Registering taxonomy...", len(rows))

    pairs_to_insert = {}
    routes_to_insert = {}
    hops_to_insert = []

    for chain_id, origin, dest, pools, first_seen, last_seen, freq in rows:
        origin_clean = (origin or '').lower()
        dest_clean = (dest or '').lower()
        pid = compute_pair_id(chain_id, origin_clean, dest_clean)
        rid = compute_route_id(pid, pools)

        pair_key = (pid, chain_id, origin_clean, dest_clean)
        if pair_key not in pairs_to_insert:
            pairs_to_insert[pair_key] = [first_seen, last_seen]
        else:
            if first_seen < pairs_to_insert[pair_key][0]:
                pairs_to_insert[pair_key][0] = first_seen
            if last_seen > pairs_to_insert[pair_key][1]:
                pairs_to_insert[pair_key][1] = last_seen

        route_key = (rid, pid, chain_id, len(pools), canonical_key(pid, pools))
        if route_key not in routes_to_insert:
            routes_to_insert[route_key] = [first_seen, last_seen]
        else:
            if first_seen < routes_to_insert[route_key][0]:
                routes_to_insert[route_key][0] = first_seen
            if last_seen > routes_to_insert[route_key][1]:
                routes_to_insert[route_key][1] = last_seen

    # Bulk insert origin_destination_pair
    pair_rows = [(pid, chain_id, origin, dest, dates[0], dates[1]) for (pid, chain_id, origin, dest), dates in pairs_to_insert.items()]
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO origin_destination_pair (id, chain_id, origin_contract, dest_contract, first_seen, last_seen)
        VALUES %s
        ON CONFLICT (chain_id, origin_contract, dest_contract) DO UPDATE SET
            first_seen = LEAST(origin_destination_pair.first_seen, EXCLUDED.first_seen),
            last_seen  = GREATEST(origin_destination_pair.last_seen, EXCLUDED.last_seen)
        """,
        pair_rows,
        page_size=500
    )

    # Bulk insert route
    route_rows = [(rid, pid, chain_id, hops, ckey, dates[0], dates[1]) for (rid, pid, chain_id, hops, ckey), dates in routes_to_insert.items()]
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO route (route_id, pair_id, chain_id, hops, canonical_key, first_seen, last_seen)
        VALUES %s
        ON CONFLICT (canonical_key) DO UPDATE SET
            first_seen = LEAST(route.first_seen, EXCLUDED.first_seen),
            last_seen  = GREATEST(route.last_seen, EXCLUDED.last_seen)
        """,
        route_rows,
        page_size=500
    )

    # Bulk insert route_hop
    hops_map = {}
    for chain_id, origin, dest, pools, first_seen, last_seen, freq in rows:
        origin_clean = (origin or '').lower()
        dest_clean = (dest or '').lower()
        pid = compute_pair_id(chain_id, origin_clean, dest_clean)
        rid = compute_route_id(pid, pools)
        if rid not in hops_map:
            hops_map[rid] = (pools, origin_clean, dest_clean)

    for rid, (pools, origin_clean, dest_clean) in hops_map.items():
        for seq, pool_id in enumerate(pools):
            hops_to_insert.append((rid, seq, pool_id, origin_clean, dest_clean))

    if hops_to_insert:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO route_hop (route_id, seq, pool_id, token_in, token_out)
            VALUES %s
            ON CONFLICT (route_id, seq) DO NOTHING
            """,
            hops_to_insert,
            page_size=1000
        )

    log.info("Top-down seed: Taxonomy updated (%d pairs, %d routes, %d hops). Attributing swaps...",
             len(pair_rows), len(route_rows), len(hops_to_insert))

    # Bulk set-based UPDATE using temporary table of seed route keys
    cur.execute("""
        CREATE TEMP TABLE seed_route_targets (
            route_id BIGINT PRIMARY KEY,
            pair_id BIGINT NOT NULL,
            pools BIGINT[] NOT NULL
        ) ON COMMIT DROP
    """)

    seed_targets = [(rid, pid, pools) for (rid, pid, chain_id, hops, ckey) in routes_to_insert.keys() for (chain_id_row, origin_row, dest_row, pools_row, f_seen, l_seen, freq_row) in rows if rid == compute_route_id(pid, pools_row)]
    # De-duplicate seed targets
    unique_seed_targets = list({t[0]: t for t in seed_targets}.values())

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO seed_route_targets (route_id, pair_id, pools) VALUES %s",
        unique_seed_targets,
        page_size=1000
    )

    # Update swaps matching candidate pool arrays
    cur.execute(f"""
        WITH tx_pools AS (
            SELECT tx_hash, ts, array_agg(pool_id ORDER BY log_index) AS pools
            FROM {target_table}
            WHERE route_id IS NULL
            GROUP BY tx_hash, ts
        ),
        matched_txs AS (
            SELECT tp.tx_hash, srt.route_id
            FROM tx_pools tp
            JOIN seed_route_targets srt ON srt.pools = tp.pools
        )
        UPDATE {target_table} s
        SET route_id = m.route_id
        FROM matched_txs m
        WHERE s.tx_hash = m.tx_hash
          AND s.route_id IS NULL
    """)

    attributed_count = cur.rowcount if cur.rowcount > 0 else 0

    # Collect affected days for daily stats rollup
    affected_days: set[str] = set()
    cur.execute(f"SELECT DISTINCT ts::date::text FROM {target_table} WHERE route_id IS NOT NULL")
    for (d_str,) in cur.fetchall():
        affected_days.add(d_str)

    log.info("Top-down seed: Successfully attributed %d swaps.", attributed_count)
    return attributed_count, affected_days
