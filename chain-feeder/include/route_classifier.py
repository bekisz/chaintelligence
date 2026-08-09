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

import logging
from collections import defaultdict
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

LEG_AMOUNT_GATE = "(amount_usd >= 10.0 OR amount_usd = 0.0 OR amount_usd IS NULL)"


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
                 ometa: Optional[Dict], dmeta: Optional[Dict], ts) -> int:
    """Find-or-create an origin_destination_pair, returning its id."""
    cur.execute(
        """
        INSERT INTO origin_destination_pair (
            chain_id, origin_contract, dest_contract,
            origin_coin_id, dest_coin_id, origin_symbol, dest_symbol,
            first_seen, last_seen
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (chain_id, origin_contract, dest_contract)
        DO UPDATE SET
            origin_coin_id = COALESCE(origin_destination_pair.origin_coin_id, EXCLUDED.origin_coin_id),
            dest_coin_id   = COALESCE(origin_destination_pair.dest_coin_id, EXCLUDED.dest_coin_id),
            origin_symbol  = COALESCE(origin_destination_pair.origin_symbol, EXCLUDED.origin_symbol),
            dest_symbol    = COALESCE(origin_destination_pair.dest_symbol, EXCLUDED.dest_symbol),
            first_seen     = LEAST(origin_destination_pair.first_seen, EXCLUDED.first_seen),
            last_seen      = GREATEST(origin_destination_pair.last_seen, EXCLUDED.last_seen)
        RETURNING id
        """,
        (chain_id, origin, dest,
         (ometa or {}).get('coin_id'), (dmeta or {}).get('coin_id'),
         (ometa or {}).get('symbol'), (dmeta or {}).get('symbol'),
         ts, ts),
    )
    return cur.fetchone()[0]


def resolve_route(cur, pair_id: int, chain_id: int, pools: List[int],
                  tokens: List[str], ts) -> int:
    """Find-or-create a route (identity = pair_id + ordered pool ids), upsert
    its hops, and return route_id."""
    route_key = canonical_key(pair_id, pools)
    cur.execute(
        """
        INSERT INTO route (pair_id, chain_id, hops, canonical_key, first_seen, last_seen)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (canonical_key)
        DO UPDATE SET
            first_seen = LEAST(route.first_seen, EXCLUDED.first_seen),
            last_seen  = GREATEST(route.last_seen, EXCLUDED.last_seen)
        RETURNING route_id
        """,
        (pair_id, chain_id, len(pools), route_key, ts, ts),
    )
    route_id = cur.fetchone()[0]
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
    return route_id


def _legs_for_txs(cur, tx_hashes: List[str]) -> List[Dict]:
    """Fetch classifier legs (with per-token addresses/symbols via pool joins)
    for a set of tx hashes, ordered by (tx_hash, log_index)."""
    cur.execute(
        """
        SELECT
            s.tx_hash, s.log_index, s.ts, s.pool_id,
            s.amount0, s.amount1, s.amount_usd,
            lp.chain_id,
            cc0.contract_address AS token0, cc1.contract_address AS token1,
            c0.symbol AS symbol0, c1.symbol AS symbol1,
            lp.coin0_id, lp.coin1_id
        FROM swaps s
        JOIN liquidity_pool lp ON s.pool_id = lp.id
        JOIN coin_contract cc0 ON cc0.coin_id = lp.coin0_id AND cc0.chain_id = lp.chain_id
        JOIN coin_contract cc1 ON cc1.coin_id = lp.coin1_id AND cc1.chain_id = lp.chain_id
        JOIN coin c0 ON c0.coin_id = lp.coin0_id
        JOIN coin c1 ON c1.coin_id = lp.coin1_id
        WHERE s.tx_hash = ANY(%s)
        ORDER BY s.tx_hash, s.log_index
        """,
        (tx_hashes,),
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


def classify_legs(cur, legs: List[Dict]) -> int:
    """Given fetched legs, register chains/pairs/routes and set swaps.route_id
    on every leg. Returns number of legs attributed."""
    by_tx: Dict[str, List[Dict]] = defaultdict(list)
    for leg in legs:
        by_tx[leg['tx_hash']].append(leg)

    updated = 0
    for tx_hash, tx_legs in by_tx.items():
        for chain in chains_in_tx(tx_legs):
            meta = _token_meta(chain['legs'])
            origin = (chain['tokens'][0] or '').lower()
            dest = (chain['tokens'][-1] or '').lower()
            chain_id = chain['legs'][0]['chain_id']
            ts = chain['legs'][0]['ts']
            pair_id = resolve_pair(cur, chain_id, origin, dest,
                                   meta.get(origin), meta.get(dest), ts)
            route_id = resolve_route(cur, pair_id, chain_id,
                                     chain['pools'], chain['tokens'], ts)
            for leg in chain['legs']:
                cur.execute(
                    "UPDATE swaps SET route_id = %s WHERE tx_hash = %s AND log_index = %s AND ts = %s",
                    (route_id, leg['tx_hash'], leg['log_index'], leg['ts']),
                )
                updated += 1
    return updated


def classify_tx_hashes(cur, tx_hashes: List[str]) -> int:
    """Pull legs for a set of tx hashes and classify them. Idempotent."""
    if not tx_hashes:
        return 0
    legs = _legs_for_txs(cur, tx_hashes)
    if not legs:
        return 0
    return classify_legs(cur, legs)


def recompute_daily_stats(cur, days: List[str]) -> int:
    """Recompute route_daily_stats for a set of ISO days ('YYYY-MM-DD').

    The table is derived (materialized), so DELETE+INSERT is correct regardless
    of classification order — late mixed-protocol legs reclassify a tx's route.
    """
    if not days:
        return 0
    start = min(days)
    end = max(days)
    cur.execute(
        "DELETE FROM route_daily_stats WHERE day >= %s AND day <= %s",
        (start, end),
    )
    cur.execute(
        f"""
        INSERT INTO route_daily_stats (route_id, day, tx_count, swap_count, volume_usd)
        SELECT
            s.route_id,
            s.ts::date AS day,
            count(DISTINCT s.tx_hash) AS tx_count,
            count(*) AS swap_count,
            sum(CASE
                WHEN s.amount0 > 0 AND cc0.contract_address = p.origin_contract THEN s.amount_usd
                WHEN s.amount1 > 0 AND cc1.contract_address = p.origin_contract THEN s.amount_usd
                ELSE 0 END) AS volume_usd
        FROM swaps s
        JOIN liquidity_pool lp ON s.pool_id = lp.id
        JOIN route r ON s.route_id = r.route_id
        JOIN origin_destination_pair p ON r.pair_id = p.id
        LEFT JOIN coin_contract cc0 ON cc0.coin_id = lp.coin0_id AND cc0.chain_id = lp.chain_id
        LEFT JOIN coin_contract cc1 ON cc1.coin_id = lp.coin1_id AND cc1.chain_id = lp.chain_id
        WHERE s.route_id IS NOT NULL
          AND s.ts >= date(%s) AND s.ts < date(%s) + interval '1 day'
          AND ({LEG_AMOUNT_GATE})
        GROUP BY s.route_id, s.ts::date
        """,
        (start, end),
    )
    return cur.rowcount
