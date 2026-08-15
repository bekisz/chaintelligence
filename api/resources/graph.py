"""DB-backed loader that assembles the Chaintelligence object graph.

Given a set of ``od`` rows (already fetched by the endpoint) plus the parsed
`?include=` paths, this module loads the requested relatives (routes, hops,
pools, coins, contracts, families, chains) with batched queries and returns
the JSON:API compound document via serializer.build_document.

The loader is deliberately schema-driven: it only loads what the include paths
ask for, so a slim `?include=` stays one query and a full drill-down stays a
handful of batched queries.
"""

import asyncio
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

from serializer import (
    apply_fields, build_document, include_matches_any, make_resource,
    parse_fields, parse_include, rel_data, rel_data_many,
)

# --- od row shape ---------------------------------------------------------
# Endpoints pass od rows as dicts with these keys (already computed, incl.
# chain name + od_hash):

OD_KEYS = (
    'od_hash', 'chain_id', 'chain', 'origin_coin_contract_address',
    'destination_coin_contract_address', 'origin_coin_id', 'dest_coin_id',
    'origin_symbol', 'dest_symbol', 'first_seen', 'last_seen',
)


def _route_hash(route_id: Optional[int]) -> Optional[str]:
    """Render a signed 64-bit route id as its 16-char lowercase hex hash.

    Route ids are signed 64-bit hashes; the JSON:API route resource id (and
    the /api/routes/{route_hash} path) use the hex form so it survives JS
    Number precision and reads like an address."""
    if route_id is None:
        return None
    return format((route_id & ((1 << 64) - 1)), '016x')


def _pool_fee_rate(fee_bps: Optional[float]) -> float:
    """Pool fee tier as a fraction. No fee_bps (Dynamic) counts as 0.02%,
    mirroring `parse_fee_rate` in api/main.py and fetch_pool_stats."""
    if fee_bps is None:
        return 0.0002
    return float(fee_bps) / 10000.0


def _compute_pool_apr(volume_usd: float, tvl_usd: float, fee_bps: Optional[float],
                      window: Tuple[str, str]) -> Optional[float]:
    """APR over a window, matching `/api/routes/analyze`'s pool APR.

    apr = (volume * fee_rate / tvl) * (365 / days), with the same unreliability
    guard: a TVL <= $1, or below 5% of average daily volume, yields None (no APR).
    """
    days = (date.fromisoformat(window[1]) - date.fromisoformat(window[0])).days
    days = max(1, days)
    fee_rate = _pool_fee_rate(fee_bps)
    if volume_usd <= 0 or tvl_usd <= 1.0:
        return None
    if tvl_usd < (volume_usd / days) * 0.05:
        return None
    return (volume_usd * fee_rate / tvl_usd) * (365.0 / days)


def _get_conn():
    from postgres_fetcher import get_conn
    return get_conn()


def _fetch_routes(pair_ids: List[int]) -> Dict[int, List[dict]]:
    """route dicts keyed by pair_id. Chain name resolved via JOIN."""
    if not pair_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.route_id, r.pair_id, r.hops, r.chain_id, ch.name AS chain,
                   r.first_seen, r.last_seen
            FROM route r
            JOIN chain ch ON r.chain_id = ch.id
            WHERE r.pair_id = ANY(%s)
            ORDER BY r.pair_id, r.hops, r.route_id
        """, (pair_ids,))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, List[dict]] = {}
    for route_id, pair_id, hops, chain_id, chain, first_seen, last_seen in rows:
        out.setdefault(pair_id, []).append({
            'route_id': route_id,
            'pair_id': pair_id,
            'hops': int(hops or 1),
            'chain_id': chain_id,
            'chain': chain,
            'first_seen': first_seen.isoformat() if first_seen else None,
            'last_seen': last_seen.isoformat() if last_seen else None,
        })
    return out


def _fetch_daily_stats(route_ids: List[int]) -> Dict[int, List[dict]]:
    """route_daily_stats rows keyed by route_id, ordered by day."""
    if not route_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT route_id, day, tx_count, swap_count, volume_usd, fees_usd
            FROM route_daily_stats
            WHERE route_id = ANY(%s)
            ORDER BY route_id, day
        """, (route_ids,))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, List[dict]] = {}
    for route_id, day, tx_count, swap_count, volume_usd, fees_usd in rows:
        out.setdefault(route_id, []).append({
            'day': day.isoformat() if hasattr(day, 'isoformat') else str(day),
            'tx_count': tx_count or 0,
            'swap_count': swap_count or 0,
            'volume_usd': float(volume_usd) if volume_usd is not None else 0.0,
            'fees_usd': float(fees_usd) if fees_usd is not None else 0.0,
        })
    return out


def _fetch_daily_stats_buckets(route_ids: List[int]) -> Dict[int, List[dict]]:
    """route_daily_stats_bucket rows keyed by route_id, ordered by day, bucket."""
    if not route_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT route_id, day, bucket_index, tx_count, sample_count,
                   volume_usd, fees_usd, log_sum, log_sum2
            FROM route_daily_stats_bucket
            WHERE route_id = ANY(%s)
            ORDER BY route_id, day, bucket_index
        """, (route_ids,))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, List[dict]] = {}
    for (route_id, day, bucket_index, tx_count, sample_count,
         volume_usd, fees_usd, log_sum, log_sum2) in rows:
        out.setdefault(route_id, []).append({
            'day': day.isoformat() if hasattr(day, 'isoformat') else str(day),
            'bucket_index': int(bucket_index),
            'tx_count': tx_count or 0,
            'sample_count': sample_count or 0,
            'volume_usd': float(volume_usd) if volume_usd is not None else 0.0,
            'fees_usd': float(fees_usd) if fees_usd is not None else 0.0,
            'log_sum': float(log_sum) if log_sum is not None else None,
            'log_sum2': float(log_sum2) if log_sum2 is not None else None,
        })
    return out


def _fetch_pool_daily_stats(pool_ids: List[int]) -> Dict[int, List[dict]]:
    """liquidity_pool_daily_stats rows keyed by pool_id, ordered by day."""
    if not pool_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT pool_id, day, tx_count, volume_usd, tvl_usd
            FROM liquidity_pool_daily_stats
            WHERE pool_id = ANY(%s)
            ORDER BY pool_id, day
        """, (pool_ids,))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, List[dict]] = {}
    for pool_id, day, tx_count, volume_usd, tvl_usd in rows:
        out.setdefault(pool_id, []).append({
            'day': day.isoformat() if hasattr(day, 'isoformat') else str(day),
            'tx_count': tx_count or 0,
            'volume_usd': float(volume_usd) if volume_usd is not None else 0.0,
            'tvl_usd': float(tvl_usd) if tvl_usd is not None else None,
        })
    return out


def _fetch_pool_daily_stats_buckets(pool_ids: List[int]) -> Dict[int, List[dict]]:
    """liquidity_pool_daily_stats_bucket rows keyed by pool_id, ordered by day, bucket."""
    if not pool_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT pool_id, day, bucket_index, tx_count, sample_count,
                   volume_usd, fees_usd, log_sum, log_sum2
            FROM liquidity_pool_daily_stats_bucket
            WHERE pool_id = ANY(%s)
            ORDER BY pool_id, day, bucket_index
        """, (pool_ids,))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, List[dict]] = {}
    for (pool_id, day, bucket_index, tx_count, sample_count,
         volume_usd, fees_usd, log_sum, log_sum2) in rows:
        out.setdefault(pool_id, []).append({
            'day': day.isoformat() if hasattr(day, 'isoformat') else str(day),
            'bucket_index': int(bucket_index),
            'tx_count': tx_count or 0,
            'sample_count': sample_count or 0,
            'volume_usd': float(volume_usd) if volume_usd is not None else 0.0,
            'fees_usd': float(fees_usd) if fees_usd is not None else 0.0,
            'log_sum': float(log_sum) if log_sum is not None else None,
            'log_sum2': float(log_sum2) if log_sum2 is not None else None,
        })
    return out


def _fetch_route_stats_range(route_ids: List[int]) -> Optional[Tuple[str, str]]:
    """(min_day, max_day) of route_daily_stats for the routes, or None."""
    if not route_ids:
        return None
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MIN(day), MAX(day) FROM route_daily_stats WHERE route_id = ANY(%s)",
                    (route_ids,))
        row = cur.fetchone()
        cur.close()
    if not row or row[0] is None:
        return None
    return (row[0].isoformat(), row[1].isoformat())


def _fetch_pool_stats_range(pool_ids: List[int]) -> Optional[Tuple[str, str]]:
    """(min_day, max_day) of liquidity_pool_daily_stats for the pools, or None."""
    if not pool_ids:
        return None
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MIN(day), MAX(day) FROM liquidity_pool_daily_stats WHERE pool_id = ANY(%s)",
                    (pool_ids,))
        row = cur.fetchone()
        cur.close()
    if not row or row[0] is None:
        return None
    return (row[0].isoformat(), row[1].isoformat())


def _fetch_route_window_stats(route_ids: List[int], start_date, end_date) -> Dict[int, dict]:
    """Aggregated window sums per route from route_daily_stats.

    Returns {route_id: {tx_count, swap_count, volume_usd, fees_usd, last_day}}.
    """
    if not route_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT route_id,
                   COALESCE(SUM(tx_count), 0),
                   COALESCE(SUM(swap_count), 0),
                   COALESCE(SUM(volume_usd), 0),
                   COALESCE(SUM(fees_usd), 0),
                   MAX(day) AS last_day
            FROM route_daily_stats
            WHERE route_id = ANY(%s)
              AND day >= %s::date AND day <= %s::date
            GROUP BY route_id
        """, (route_ids, start_date, end_date))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, dict] = {}
    for route_id, tx_count, swap_count, volume_usd, fees_usd, last_day in rows:
        out[route_id] = {
            'tx_count': int(tx_count or 0),
            'swap_count': int(swap_count or 0),
            'volume_usd': float(volume_usd or 0),
            'fees_usd': float(fees_usd or 0),
            'last_day': last_day,
        }
    return out


def _fetch_route_pair_volume(route_rows: List[dict], start_date, end_date) -> Dict[int, float]:
    """Pair-total volume per route over the window, keyed by route_id.

    Sums route_daily_stats.volume_usd across every route sharing the same
    pair_id, so `pct_volume` can express a route's share of its pair's flow.
    """
    if not route_rows:
        return {}
    route_ids = [r['route_id'] for r in route_rows]
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.pair_id, COALESCE(SUM(rs.volume_usd), 0) AS pair_vol
            FROM route_daily_stats rs
            JOIN route r ON rs.route_id = r.route_id
            WHERE r.pair_id = ANY(%s)
              AND rs.day >= %s::date AND rs.day <= %s::date
            GROUP BY r.pair_id
        """, ([r['pair_id'] for r in route_rows], start_date, end_date))
        pair_vol = {row[0]: float(row[1]) for row in cur.fetchall()}
        cur.close()
    by_route = {}
    for r in route_rows:
        by_route[r['route_id']] = pair_vol.get(r['pair_id'], 0.0)
    return by_route


def _fetch_route_cum_fee(route_ids: List[int]) -> Dict[int, float]:
    """Cumulative fee fraction across a route's hops (sum of pool fee_bps / 10000).

    Mirrors the `/api/routes/analyze` `cum_fee` used for `market_size`: each hop
    contributes its fee tier; a pool with no fee_bps (Dynamic) counts as 2 bps
    (0.02%), matching analyze's parse_fee_rate fallback.
    """
    if not route_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT h.route_id, COALESCE(SUM(COALESCE(lp.fee_bps, 2)), 0) AS total_bps
            FROM route_hop h
            JOIN liquidity_pool lp ON h.pool_id = lp.id
            WHERE h.route_id = ANY(%s)
            GROUP BY h.route_id
        """, (route_ids,))
        rows = cur.fetchall()
        cur.close()
    return {route_id: float(total_bps or 0) / 10000.0 for route_id, total_bps in rows}


def _fetch_pool_window_stats(pool_ids: List[int], start_date, end_date) -> Dict[int, dict]:
    """Aggregated window sums per pool from liquidity_pool_daily_stats.

    Returns {pool_id: {tx_count, volume_usd, tvl_usd, last_day}} where tvl_usd
    is the row-count-weighted average of non-zero TVL over the window (with a
    latest-snapshot fallback when the window has no non-zero TVL), matching the
    APR math in `api/routing/postgres_fetcher.fetch_pool_stats`.
    """
    if not pool_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT pool_id,
                   COALESCE(SUM(tx_count), 0),
                   COALESCE(SUM(volume_usd), 0),
                   AVG(tvl_usd) FILTER (WHERE tvl_usd <> 0) AS avg_tvl,
                   COUNT(*) FILTER (WHERE tvl_usd <> 0) AS n_rows,
                   MAX(day) AS last_day
            FROM liquidity_pool_daily_stats
            WHERE pool_id = ANY(%s)
              AND day >= %s::date AND day <= %s::date
            GROUP BY pool_id
        """, (pool_ids, start_date, end_date))
        rows = cur.fetchall()

        latest_tvl: Dict[int, float] = {}
        cur.execute("""
            SELECT DISTINCT ON (pool_id) pool_id, ABS(tvl_usd) AS tvl
            FROM liquidity_pool_daily_stats
            WHERE pool_id = ANY(%s) AND tvl_usd <> 0
            ORDER BY pool_id, day DESC
        """, (pool_ids,))
        for pool_id, tvl in cur.fetchall():
            latest_tvl[pool_id] = float(tvl or 0)
        cur.close()

    out: Dict[int, dict] = {}
    for pool_id, tx_count, volume_usd, avg_tvl, n_rows, last_day in rows:
        tvl = float(avg_tvl) if avg_tvl is not None else 0.0
        if tvl <= 1.0:
            tvl = latest_tvl.get(pool_id, 0.0)
        out[pool_id] = {
            'tx_count': int(tx_count or 0),
            'volume_usd': float(volume_usd or 0),
            'tvl_usd': tvl,
            'last_day': last_day,
        }
    return out


def _fetch_hops(route_ids: List[int]) -> Dict[int, List[dict]]:
    """hop dicts keyed by route_id, with token coin ids resolved."""
    if not route_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT h.route_id, h.seq, h.pool_id, h.token_in, h.token_out,
                   ci.symbol AS token_in_symbol, co.symbol AS token_out_symbol,
                   cic.coin_id AS token_in_coin_id, coc.coin_id AS token_out_coin_id
            FROM route_hop h
            LEFT JOIN liquidity_pool lp ON h.pool_id = lp.id
            LEFT JOIN coin_contract cic ON LOWER(cic.contract_address) = LOWER(h.token_in) AND cic.chain_id = lp.chain_id
            LEFT JOIN coin ci ON cic.coin_id = ci.coin_id
            LEFT JOIN coin_contract coc ON LOWER(coc.contract_address) = LOWER(h.token_out) AND coc.chain_id = lp.chain_id
            LEFT JOIN coin co ON coc.coin_id = co.coin_id
            WHERE h.route_id = ANY(%s)
            ORDER BY h.route_id, h.seq
        """, (route_ids,))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, List[dict]] = {}
    for (route_id, seq, pool_id, token_in, token_out,
         token_in_symbol, token_out_symbol, token_in_coin_id, token_out_coin_id) in rows:
        out.setdefault(route_id, []).append({
            'seq': int(seq),
            'pool_id': pool_id,
            'token_in': token_in,
            'token_out': token_out,
            'token_in_symbol': token_in_symbol,
            'token_out_symbol': token_out_symbol,
            'token_in_coin_id': token_in_coin_id,
            'token_out_coin_id': token_out_coin_id,
        })
    return out


def _fetch_pools(pool_ids: List[int]) -> Dict[int, dict]:
    """pool dicts keyed by pool id, incl. chain + protocol + latest stats."""
    if not pool_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT lp.id, lp.pool_address, lp.pool_id AS v4_pool_id,
                   lp.fee_bps, ch.name AS chain, ch.id AS chain_id,
                   pr.name AS protocol,
                   lp.coin0_id, lp.coin1_id, lp.created_at
            FROM liquidity_pool lp
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            WHERE lp.id = ANY(%s)
        """, (pool_ids,))
        pool_rows = cur.fetchall()

        stats: Dict[int, dict] = {}
        if pool_rows:
            ids = [r[0] for r in pool_rows]
            cur.execute("""
                SELECT DISTINCT ON (pool_id) pool_id, day, tvl_usd, volume_usd, tx_count
                FROM liquidity_pool_daily_stats
                WHERE pool_id = ANY(%s)
                ORDER BY pool_id, day DESC
            """, (ids,))
            for pool_id, day, tvl, vol, txs in cur.fetchall():
                stats[pool_id] = {
                    'tvl_usd': float(tvl) if tvl is not None else None,
                    'volume_usd': float(vol) if vol is not None else None,
                    'tx_count': txs or 0,
                }
        cur.close()

    out = {}
    for (pool_id, pool_address, v4_pool_id, fee_bps, chain, chain_id, protocol,
         coin0_id, coin1_id, created_at) in pool_rows:
        s = stats.get(pool_id, {})
        out[pool_id] = {
            'pool_id': pool_id,
            'pool_address': pool_address,
            'v4_pool_id': v4_pool_id,
            'fee_bps': round(fee_bps) if fee_bps is not None else None,
            'chain': chain,
            'chain_id': chain_id,
            'protocol': protocol,
            'coin0_id': coin0_id,
            'coin1_id': coin1_id,
            'created_at': created_at.isoformat() if created_at else None,
            'tvl_usd': s.get('tvl_usd'),
            'volume_usd': s.get('volume_usd'),
            'tx_count': s.get('tx_count'),
        }
    return out


def _fetch_coins(coin_ids: List[int]) -> Dict[int, dict]:
    if not coin_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT c.coin_id, c.symbol, c.name, c.slug, c.hardness,
                   c.cmc_rank, c.cmc_id, c.first_historical_data,
                   c.image_url, c.price, c.price_timestamp, c.decimals,
                   c.percent_change_1h, c.percent_change_24h, c.percent_change_7d,
                   c.percent_change_30d, c.percent_change_60d, c.percent_change_90d,
                   c.market_cap, c.market_cap_dominance, c.fully_diluted_market_cap,
                   c.tvl, c.total_supply, c.circulating_supply, c.max_supply,
                   c.cmc_last_updated
            FROM coin c
            WHERE c.coin_id = ANY(%s)
        """, (coin_ids,))
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        cur.close()
    out = {}
    for row in rows:
        coin = dict(zip(cols, row))
        coin['price'] = float(coin['price']) if coin['price'] is not None else None
        for col in ('percent_change_1h', 'percent_change_24h', 'percent_change_7d',
                    'percent_change_30d', 'percent_change_60d', 'percent_change_90d',
                    'market_cap', 'market_cap_dominance', 'fully_diluted_market_cap',
                    'tvl', 'total_supply', 'circulating_supply', 'max_supply'):
            if coin[col] is not None:
                coin[col] = float(coin[col])
        for col in ('first_historical_data', 'price_timestamp', 'cmc_last_updated'):
            if coin[col] is not None:
                coin[col] = coin[col].isoformat()
        out[coin['coin_id']] = coin
    return out


def _fetch_coin_contracts(coin_ids: List[int]) -> Dict[int, List[dict]]:
    if not coin_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT cc.coin_id, ch.name AS chain, cc.contract_address, cc.decimals,
                   cc.is_native, cc.verified_at, cc.tracked, cc.source, cc.confidence_score
            FROM coin_contract cc
            JOIN chain ch ON cc.chain_id = ch.id
            WHERE cc.coin_id = ANY(%s)
            ORDER BY cc.coin_id, ch.name
        """, (coin_ids,))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, List[dict]] = {}
    for (coin_id, chain, contract_address, decimals, is_native, verified_at,
         tracked, source, confidence_score) in rows:
        out.setdefault(coin_id, []).append({
            'chain': chain,
            'contract_address': contract_address,
            'decimals': decimals,
            'is_native': bool(is_native),
            'verified_at': verified_at.isoformat() if verified_at else None,
            'tracked': bool(tracked),
            'source': source,
            'confidence_score': confidence_score,
        })
    return out


def _fetch_coin_families(coin_ids: List[int]) -> Dict[int, List[str]]:
    if not coin_ids:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT f.coin_id, UPPER(f.name) AS family
            FROM coin_family f
            WHERE f.coin_id = ANY(%s)
            ORDER BY f.coin_id, f.name
        """, (coin_ids,))
        rows = cur.fetchall()
        cur.close()
    out: Dict[int, List[str]] = {}
    for coin_id, family in rows:
        out.setdefault(coin_id, []).append(family)
    return out


def _fetch_family_members(family_names: List[str]) -> Dict[str, List[int]]:
    if not family_names:
        return {}
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT UPPER(f.name) AS family, f.coin_id
            FROM coin_family f
            WHERE UPPER(f.name) = ANY(%s)
            ORDER BY f.name, f.coin_id
        """, ([f.upper() for f in family_names],))
        rows = cur.fetchall()
        cur.close()
    out: Dict[str, List[int]] = {}
    for family, coin_id in rows:
        out.setdefault(family, []).append(coin_id)
    return out


# --- document assembly ----------------------------------------------------

def _od_attrs(od_row: dict) -> Dict[str, Any]:
    return {k: od_row.get(k) for k in OD_KEYS if k != 'od_hash'}


def build_od_documents(od_rows: List[dict], include_spec: Optional[str] = None,
                       fields_spec: Optional[str] = None,
                       links: Optional[Dict[str, Any]] = None,
                       meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a JSON:API compound document for a list of od rows.

    ``include_spec`` is the raw `?include=` value (e.g.
    "routes.hops.pool,routes.hops.pool.coin0"). Default (None) returns just
    the od roots with relationship references but no `included` resources.
    """
    paths = parse_include(include_spec, 'od')
    fields = parse_fields(fields_spec)

    included: List[dict] = []
    routes_by_pair: Dict[int, List[dict]] = {}
    hops_by_route: Dict[int, List[dict]] = {}
    pools: Dict[int, dict] = {}
    coins: Dict[int, dict] = {}
    contracts_by_coin: Dict[int, List[dict]] = {}
    families_by_coin: Dict[int, List[str]] = {}
    family_members: Dict[str, List[int]] = {}

    want_routes = include_matches_any(['routes'], paths)
    want_hops = include_matches_any(['routes', 'hops'], paths)
    want_pools = include_matches_any(['routes', 'hops', 'pool'], paths)
    want_pool_coins = include_matches_any(['routes', 'hops', 'pool', 'coin0'], paths) or \
                      include_matches_any(['routes', 'hops', 'pool', 'coin1'], paths)
    want_hop_coins = include_matches_any(['routes', 'hops', 'token_in'], paths) or \
                     include_matches_any(['routes', 'hops', 'token_out'], paths)
    want_od_coins = include_matches_any(['origin_coin'], paths) or \
                    include_matches_any(['destination_coin'], paths)
    want_daily_stats = include_matches_any(['routes', 'daily_stats'], paths)
    want_daily_stats_buckets = include_matches_any(['routes', 'daily_stats_bucket'], paths) or \
                               include_matches_any(['routes', 'daily_stats', 'daily_stats_bucket'], paths)

    pair_ids = [int(o['od_hash'], 16) if int(o['od_hash'], 16) < (1 << 63)
                else int(o['od_hash'], 16) - (1 << 64) for o in od_rows]
    od_by_pair: Dict[int, dict] = {pid: o for pid, o in zip(pair_ids, od_rows)}

    daily_stats_by_route: Dict[int, List[dict]] = {}
    daily_stats_buckets_by_route: Dict[int, List[dict]] = {}

    # Load routes/hops/pools as requested.
    if want_routes and pair_ids:
        routes_by_pair = _fetch_routes(pair_ids)
    all_routes = [r for rs in routes_by_pair.values() for r in rs]
    if want_hops and all_routes:
        route_ids = [r['route_id'] for r in all_routes]
        hops_by_route = _fetch_hops(route_ids)
    all_hops = [h for hs in hops_by_route.values() for h in hs]
    if want_pools and all_hops:
        pool_ids = list({h['pool_id'] for h in all_hops if h['pool_id']})
        pools = _fetch_pools(pool_ids)
    if want_daily_stats and all_routes:
        daily_stats_by_route = _fetch_daily_stats([r['route_id'] for r in all_routes])
    if want_daily_stats_buckets and all_routes:
        daily_stats_buckets_by_route = _fetch_daily_stats_buckets([r['route_id'] for r in all_routes])

    # Collect coin ids referenced anywhere the include asks for coins.
    coin_ids: Set[int] = set()
    if want_od_coins:
        for o in od_rows:
            if o.get('origin_coin_id'):
                coin_ids.add(o['origin_coin_id'])
            if o.get('dest_coin_id'):
                coin_ids.add(o['dest_coin_id'])
    if want_pool_coins:
        for p in pools.values():
            if p.get('coin0_id'):
                coin_ids.add(p['coin0_id'])
            if p.get('coin1_id'):
                coin_ids.add(p['coin1_id'])
    if want_hop_coins:
        for h in all_hops:
            if h.get('token_in_coin_id'):
                coin_ids.add(h['token_in_coin_id'])
            if h.get('token_out_coin_id'):
                coin_ids.add(h['token_out_coin_id'])

    if coin_ids:
        coin_ids_l = list(coin_ids)
        coins = _fetch_coins(coin_ids_l)
        # contracts/families are leaf relationships on coins, reached at the
        # tail of any include path that descends into a coin (e.g.
        # origin_coin.contracts or routes.hops.pool.coin0.families).
        want_contracts = any(p and p[-1] == 'contracts' for p in paths)
        want_families = any(p and p[-1] == 'families' for p in paths)
        if want_contracts:
            contracts_by_coin = _fetch_coin_contracts(coin_ids_l)
        if want_families:
            families_by_coin = _fetch_coin_families(coin_ids_l)
            all_fams = sorted({f for fams in families_by_coin.values() for f in fams})
            if all_fams:
                family_members = _fetch_family_members(all_fams)

    # Build included resources in a stable order.
    # routes (root first)
    for pair_id, route_list in routes_by_pair.items():
        for r in route_list:
            r_hash = _route_hash(r['route_id'])
            route_rels = {
                'pair': rel_data('od', od_by_pair.get(pair_id, {}).get('od_hash')),
                'chain': rel_data('chain', r.get('chain_id')),
                'hops': rel_data_many([('hop', f"{r_hash}:{h['seq']}") for h in hops_by_route.get(r['route_id'], [])]),
                'daily_stats': rel_data_many([('route_daily_stat', f"{r_hash}:{s['day']}") for s in daily_stats_by_route.get(r['route_id'], [])]),
                'daily_stats_bucket': rel_data_many([('route_daily_stat_bucket', f"{r_hash}:{b['day']}:{b['bucket_index']}") for b in daily_stats_buckets_by_route.get(r['route_id'], [])]),
            }
            included.append(make_resource(
                'route', r_hash,
                attributes={'hops': r['hops'], 'chain': r.get('chain'),
                            'first_seen': r.get('first_seen'), 'last_seen': r.get('last_seen')},
                relationships=route_rels,
            ))
    # hops
    for route_id, hop_list in hops_by_route.items():
        r_hash = _route_hash(route_id)
        for h in hop_list:
            hop_rels = {
                'route': rel_data('route', r_hash),
                'pool': rel_data('pool', h.get('pool_id')),
                'token_in': rel_data('coin', h.get('token_in_coin_id')),
                'token_out': rel_data('coin', h.get('token_out_coin_id')),
            }
            included.append(make_resource(
                'hop', f"{r_hash}:{h['seq']}",
                attributes={'seq': h['seq'], 'token_in': h['token_in'], 'token_out': h['token_out'],
                            'token_in_symbol': h.get('token_in_symbol'),
                            'token_out_symbol': h.get('token_out_symbol')},
                relationships=hop_rels,
            ))
    # pools
    for pool_id, p in pools.items():
        pool_rels = {
            'chain': rel_data('chain', p.get('chain_id')),
            'coin0': rel_data('coin', p.get('coin0_id')),
            'coin1': rel_data('coin', p.get('coin1_id')),
        }
        included.append(make_resource(
            'pool', pool_id,
            attributes={k: p.get(k) for k in
                        ('pool_address', 'pool_id', 'fee_bps', 'tvl_usd', 'volume_usd',
                         'tx_count', 'created_at')},
            relationships=pool_rels,
        ))
    # coins
    for coin_id, c in coins.items():
        coin_rels = {
            'contracts': rel_data_many([('coin_contract', f"{coin_id}:{cc['chain']}") for cc in contracts_by_coin.get(coin_id, [])]),
            'families': rel_data_many([('coin_family', f) for f in families_by_coin.get(coin_id, [])]),
        }
        included.append(make_resource(
            'coin', coin_id,
            attributes={k: c.get(k) for k in
                        ('symbol', 'name', 'slug', 'price', 'market_cap', 'cmc_rank',
                         'decimals', 'image_url', 'tvl', 'total_supply',
                         'circulating_supply', 'max_supply')},
            relationships=coin_rels,
        ))
    # coin_contracts
    for coin_id, ccs in contracts_by_coin.items():
        for cc in ccs:
            included.append(make_resource(
                'coin_contract', f"{coin_id}:{cc['chain']}",
                attributes={k: cc.get(k) for k in
                            ('chain', 'contract_address', 'decimals', 'is_native',
                             'tracked', 'source', 'confidence_score')},
                relationships={'coin': rel_data('coin', coin_id)},
            ))
    # coin_families + members
    seen_families: Set[str] = set()
    for coin_id, fams in families_by_coin.items():
        for fam in fams:
            if fam in seen_families:
                continue
            seen_families.add(fam)
            included.append(make_resource(
                'coin_family', fam,
                attributes={'name': fam},
                relationships={
                    'members': rel_data_many([('coin', mid) for mid in family_members.get(fam, [])]),
                },
            ))
    # route daily stats + buckets
    _append_daily_stats(daily_stats_by_route, daily_stats_buckets_by_route, included)
    _append_daily_stats_buckets(daily_stats_buckets_by_route, included)

    # Build the data list (od roots with relationship references).
    data = []
    for o in od_rows:
        od_rels = {
            'origin_coin': rel_data('coin', o.get('origin_coin_id')),
            'destination_coin': rel_data('coin', o.get('dest_coin_id')),
            'routes': rel_data_many([('route', _route_hash(r['route_id'])) for r in
                                     routes_by_pair.get(_pair_id_for(o), [])]),
        }
        data.append(make_resource('od', o['od_hash'], attributes=_od_attrs(o), relationships=od_rels))

    doc = build_document(data if len(data) != 1 else data[0], included=included,
                         links=links, meta=meta)
    apply_fields(doc, fields)
    return doc


def _pair_id_for(o: dict) -> int:
    pid = int(o['od_hash'], 16)
    if pid >= (1 << 63):
        pid -= (1 << 64)
    return pid


def build_route_documents(route_rows: List[dict], include_spec: Optional[str] = None,
                          fields_spec: Optional[str] = None,
                          links: Optional[Dict[str, Any]] = None,
                          meta: Optional[Dict[str, Any]] = None,
                          window: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
    """Compound document for route rows (dicts with route_id/pair_id/hops/...).

    ``window`` optionally supplies (start_date, end_date) ISO strings. When set,
    each route resource gains a `window_stats` attribute with the aggregated
    sums/derived metrics over that range (tx/swap counts, volume, fees,
    market_size, avg_volume, pct_volume, last_activity) — the same numbers
    `/api/routes/analyze` reports per route, computed from the pre-aggregated
    `route_daily_stats` table.
    """
    paths = parse_include(include_spec, 'route')
    fields = parse_fields(fields_spec)
    included: List[dict] = []

    route_ids = [r['route_id'] for r in route_rows]
    hops_by_route: Dict[int, List[dict]] = {}
    if include_matches_any(['hops'], paths) and route_ids:
        hops_by_route = _fetch_hops(route_ids)
    all_hops = [h for hs in hops_by_route.values() for h in hs]

    daily_stats_by_route: Dict[int, List[dict]] = {}
    daily_stats_buckets_by_route: Dict[int, List[dict]] = {}
    if include_matches_any(['daily_stats'], paths) and route_ids:
        daily_stats_by_route = _fetch_daily_stats(route_ids)
    if (include_matches_any(['daily_stats_bucket'], paths) or
            include_matches_any(['daily_stats', 'daily_stats_bucket'], paths)) and route_ids:
        daily_stats_buckets_by_route = _fetch_daily_stats_buckets(route_ids)

    window_stats_by_route: Dict[int, dict] = {}
    if window is not None and route_ids:
        start_date, end_date = window
        window_stats_by_route = _fetch_route_window_stats(route_ids, start_date, end_date)
    cum_fees: Dict[int, float] = {}
    pair_vol_by_route: Dict[int, float] = {}
    if window_stats_by_route:
        cum_fees = _fetch_route_cum_fee(list(window_stats_by_route.keys()))
        pair_vol_by_route = _fetch_route_pair_volume(route_rows, *window)

    pools: Dict[int, dict] = {}
    if include_matches_any(['hops', 'pool'], paths) and all_hops:
        pool_ids = list({h['pool_id'] for h in all_hops if h['pool_id']})
        pools = _fetch_pools(pool_ids)

    coins: Dict[int, dict] = {}
    contracts_by_coin: Dict[int, List[dict]] = {}
    families_by_coin: Dict[int, List[str]] = {}
    family_members: Dict[str, List[int]] = {}
    coin_ids: Set[int] = set()
    if include_matches_any(['hops', 'pool', 'coin0'], paths) or \
       include_matches_any(['hops', 'pool', 'coin1'], paths):
        for p in pools.values():
            if p.get('coin0_id'):
                coin_ids.add(p['coin0_id'])
            if p.get('coin1_id'):
                coin_ids.add(p['coin1_id'])
    if coin_ids:
        coin_ids_l = list(coin_ids)
        coins = _fetch_coins(coin_ids_l)
        if any(p and p[-1] == 'contracts' for p in paths):
            contracts_by_coin = _fetch_coin_contracts(coin_ids_l)
        if any(p and p[-1] == 'families' for p in paths):
            families_by_coin = _fetch_coin_families(coin_ids_l)
            all_fams = sorted({f for fams in families_by_coin.values() for f in fams})
            if all_fams:
                family_members = _fetch_family_members(all_fams)

    data = []
    for r in route_rows:
        r_hash = _route_hash(r['route_id'])
        route_attrs = {'hops': r.get('hops'), 'chain': r.get('chain'),
                       'first_seen': r.get('first_seen'),
                       'last_seen': r.get('last_seen')}
        ws = window_stats_by_route.get(r['route_id'])
        if ws:
            vol = ws['volume_usd']
            tc = ws['tx_count']
            cum_fee = cum_fees.get(r['route_id'], 0.0)
            pair_vol = pair_vol_by_route.get(r['route_id'], 0.0)
            route_attrs['window_stats'] = {
                'start_date': window[0],
                'end_date': window[1],
                'tx_count': tc,
                'swap_count': ws['swap_count'],
                'volume_usd': vol,
                'fees_usd': ws['fees_usd'],
                'market_size': round(vol * cum_fee, 6),
                'avg_volume': round(vol / tc, 6) if tc else 0.0,
                'pct_volume': round(vol / pair_vol * 100, 6) if pair_vol else 0.0,
                'last_activity': ws['last_day'].isoformat() if ws['last_day'] else None,
            }
        route_rels = {
            'pair': rel_data('od', r.get('od_hash')),
            'chain': rel_data('chain', r.get('chain_id')),
            'hops': rel_data_many([('hop', f"{r_hash}:{h['seq']}") for h in hops_by_route.get(r['route_id'], [])]),
            'daily_stats': rel_data_many([('route_daily_stat', f"{r_hash}:{s['day']}") for s in daily_stats_by_route.get(r['route_id'], [])]),
            'daily_stats_bucket': rel_data_many([('route_daily_stat_bucket', f"{r_hash}:{b['day']}:{b['bucket_index']}") for b in daily_stats_buckets_by_route.get(r['route_id'], [])]),
        }
        data.append(make_resource('route', r_hash,
                                  attributes=route_attrs,
                                  relationships=route_rels))
    for route_id, hop_list in hops_by_route.items():
        r_hash = _route_hash(route_id)
        for h in hop_list:
            included.append(make_resource(
                'hop', f"{r_hash}:{h['seq']}",
                attributes={'seq': h['seq'], 'token_in': h['token_in'], 'token_out': h['token_out'],
                            'token_in_symbol': h.get('token_in_symbol'),
                            'token_out_symbol': h.get('token_out_symbol')},
                relationships={
                    'route': rel_data('route', r_hash),
                    'pool': rel_data('pool', h.get('pool_id')),
                    'token_in': rel_data('coin', h.get('token_in_coin_id')),
                    'token_out': rel_data('coin', h.get('token_out_coin_id')),
                },
            ))
    _append_pools(pools, included)
    _append_coins(coins, contracts_by_coin, families_by_coin, included)
    _append_contracts(contracts_by_coin, included)
    _append_families(families_by_coin, family_members, included)
    _append_daily_stats(daily_stats_by_route, daily_stats_buckets_by_route, included)
    _append_daily_stats_buckets(daily_stats_buckets_by_route, included)

    doc = build_document(data if len(data) != 1 else data[0],
                         included=included, links=links, meta=meta)
    apply_fields(doc, fields)
    return doc


def build_pool_documents(pool_rows: List[dict], include_spec: Optional[str] = None,
                         fields_spec: Optional[str] = None,
                         links: Optional[Dict[str, Any]] = None,
                         meta: Optional[Dict[str, Any]] = None,
                         window: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
    """Compound document for pool rows (dicts with pool_id/coin0_id/...).

    ``window`` optionally supplies (start_date, end_date) ISO strings. When set,
    each pool resource gains a `window_stats` attribute (window sums: tx_count,
    volume_usd, tvl_usd, fees_usd) and an `apr` attribute computed from the
    pre-aggregated `liquidity_pool_daily_stats` table with the same fee-rate /
    TVL-reliability math as `/api/routes/analyze`.
    """
    paths = parse_include(include_spec, 'pool')
    fields = parse_fields(fields_spec)
    included: List[dict] = []

    pool_ids = [p['pool_id'] for p in pool_rows]
    daily_stats_by_pool: Dict[int, List[dict]] = {}
    daily_stats_buckets_by_pool: Dict[int, List[dict]] = {}
    if include_matches_any(['daily_stats'], paths) and pool_ids:
        daily_stats_by_pool = _fetch_pool_daily_stats(pool_ids)
    if (include_matches_any(['daily_stats_bucket'], paths) or
            include_matches_any(['daily_stats', 'daily_stats_bucket'], paths)) and pool_ids:
        daily_stats_buckets_by_pool = _fetch_pool_daily_stats_buckets(pool_ids)

    window_stats_by_pool: Dict[int, dict] = {}
    if window is not None and pool_ids:
        window_stats_by_pool = _fetch_pool_window_stats(pool_ids, *window)

    coins: Dict[int, dict] = {}
    contracts_by_coin: Dict[int, List[dict]] = {}
    families_by_coin: Dict[int, List[str]] = {}
    family_members: Dict[str, List[int]] = {}
    coin_ids: Set[int] = set()
    if include_matches_any(['coin0'], paths) or include_matches_any(['coin1'], paths):
        for p in pool_rows:
            if p.get('coin0_id'):
                coin_ids.add(p['coin0_id'])
            if p.get('coin1_id'):
                coin_ids.add(p['coin1_id'])
    if coin_ids:
        coin_ids_l = list(coin_ids)
        coins = _fetch_coins(coin_ids_l)
        if any(p and p[-1] == 'contracts' for p in paths):
            contracts_by_coin = _fetch_coin_contracts(coin_ids_l)
        if any(p and p[-1] == 'families' for p in paths):
            families_by_coin = _fetch_coin_families(coin_ids_l)
            all_fams = sorted({f for fams in families_by_coin.values() for f in fams})
            if all_fams:
                family_members = _fetch_family_members(all_fams)

    data = []
    for p in pool_rows:
        pool_rels = {
            'chain': rel_data('chain', p.get('chain_id')),
            'coin0': rel_data('coin', p.get('coin0_id')),
            'coin1': rel_data('coin', p.get('coin1_id')),
            'daily_stats': rel_data_many([('pool_daily_stat', f"{p['pool_id']}:{s['day']}") for s in daily_stats_by_pool.get(p['pool_id'], [])]),
            'daily_stats_bucket': rel_data_many([('pool_daily_stat_bucket', f"{p['pool_id']}:{b['day']}:{b['bucket_index']}") for b in daily_stats_buckets_by_pool.get(p['pool_id'], [])]),
        }
        attrs = {
            'pool_address': p.get('pool_address'),
            'pool_id': p.get('v4_pool_id'),
            'fee_bps': p.get('fee_bps'),
            'fee_tier': p.get('fee_tier'),
            'protocol': p.get('protocol'),
            'canonical_address': p.get('canonical_address'),
            'defillama_uuid': p.get('defillama_uuid'),
            'tvl_usd': p.get('tvl_usd'),
            'volume_usd': p.get('volume_usd'),
            'volume_usd_24h': p.get('volume_usd_24h'),
            'tx_count': p.get('tx_count'),
            'created_at': p.get('created_at'),
            'links': p.get('links'),
            'history': p.get('history'),
        }
        ws = window_stats_by_pool.get(p['pool_id'])
        if ws:
            attrs['window_stats'] = {
                'start_date': window[0],
                'end_date': window[1],
                'tx_count': ws['tx_count'],
                'volume_usd': ws['volume_usd'],
                'tvl_usd': round(ws['tvl_usd'], 6),
                'fees_usd': round(ws['volume_usd'] * _pool_fee_rate(p.get('fee_bps')), 6),
            }
            attrs['apr'] = _compute_pool_apr(
                ws['volume_usd'], ws['tvl_usd'], p.get('fee_bps'), window,
            )
        data.append(make_resource(
            'pool', p['pool_id'],
            attributes={k: v for k, v in attrs.items() if v is not None},
            relationships=pool_rels,
        ))
    _append_coins(coins, contracts_by_coin, families_by_coin, included)
    _append_contracts(contracts_by_coin, included)
    _append_families(families_by_coin, family_members, included)
    _append_pool_daily_stats(daily_stats_by_pool, daily_stats_buckets_by_pool, included)
    _append_pool_daily_stats_buckets(daily_stats_buckets_by_pool, included)

    doc = build_document(data if len(data) != 1 else data[0],
                         included=included, links=links, meta=meta)
    apply_fields(doc, fields)
    return doc


def build_coin_documents(coin_rows: List[dict], include_spec: Optional[str] = None,
                         fields_spec: Optional[str] = None,
                         links: Optional[Dict[str, Any]] = None,
                         meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compound document for coin rows (dicts with coin_id/symbol/...)."""
    paths = parse_include(include_spec, 'coin')
    fields = parse_fields(fields_spec)
    included: List[dict] = []

    coin_ids = [c['coin_id'] for c in coin_rows]
    contracts_by_coin: Dict[int, List[dict]] = {}
    families_by_coin: Dict[int, List[str]] = {}
    family_members: Dict[str, List[int]] = {}
    if any(p and p[-1] == 'contracts' for p in paths) and coin_ids:
        contracts_by_coin = _fetch_coin_contracts(coin_ids)
    if any(p and p[-1] == 'families' for p in paths) and coin_ids:
        families_by_coin = _fetch_coin_families(coin_ids)
        all_fams = sorted({f for fams in families_by_coin.values() for f in fams})
        if all_fams:
            family_members = _fetch_family_members(all_fams)

    data = []
    for c in coin_rows:
        coin_rels = {
            'contracts': rel_data_many([('coin_contract', f"{c['coin_id']}:{cc['chain']}") for cc in contracts_by_coin.get(c['coin_id'], [])]),
            'families': rel_data_many([('coin_family', f) for f in families_by_coin.get(c['coin_id'], [])]),
        }
        data.append(make_resource(
            'coin', c['coin_id'],
            attributes={k: c.get(k) for k in
                        ('symbol', 'name', 'slug', 'price', 'market_cap', 'cmc_rank',
                         'decimals', 'image_url', 'tvl', 'total_supply',
                         'circulating_supply', 'max_supply')},
            relationships=coin_rels,
        ))
    _append_contracts(contracts_by_coin, included)
    _append_families(families_by_coin, family_members, included)

    doc = build_document(data if len(data) != 1 else data[0],
                         included=included, links=links, meta=meta)
    apply_fields(doc, fields)
    return doc


def build_coin_family_documents(family_rows: List[dict], include_spec: Optional[str] = None,
                                fields_spec: Optional[str] = None,
                                links: Optional[Dict[str, Any]] = None,
                                meta: Optional[Dict[str, Any]] = None,
                                member_attrs: Optional[Tuple[str, ...]] = None) -> Dict[str, Any]:
    """Compound document for coin_family rows (dicts with family/...).

    ``member_attrs`` optionally limits the attributes emitted for member coin
    resources (defaults to the full coin attribute set). The coin-families
    list endpoint uses a slim subset so the 566-member payload stays compact.
    """
    paths = parse_include(include_spec, 'coin_family')
    fields = parse_fields(fields_spec)
    included: List[dict] = []

    family_names = [f['family'] for f in family_rows]
    family_members: Dict[str, List[int]] = {}
    coins: Dict[int, dict] = {}
    member_ids: Set[int] = set()
    if include_matches_any(['members'], paths) and family_names:
        family_members = _fetch_family_members(family_names)
        member_ids = {mid for mids in family_members.values() for mid in mids}
        if member_ids:
            coins = _fetch_coins(list(member_ids))

    data = []
    for f in family_rows:
        fam = f['family']
        data.append(make_resource(
            'coin_family', fam,
            attributes={'name': fam},
            relationships={
                'members': rel_data_many([('coin', mid) for mid in family_members.get(fam, [])]),
            },
        ))
    if member_attrs:
        for coin_id, c in coins.items():
            included.append(make_resource(
                'coin', coin_id,
                attributes={k: c.get(k) for k in member_attrs if k in c},
                relationships={
                    'contracts': rel_data_many([]),
                    'families': rel_data_many([]),
                },
            ))
    else:
        _append_coins(coins, {}, {}, included)

    doc = build_document(data if len(data) != 1 else data[0],
                         included=included, links=links, meta=meta)
    apply_fields(doc, fields)
    return doc


# --- shared included-resource appenders ------------------------------------

def _append_daily_stats(daily_stats_by_route: Dict[int, List[dict]],
                        daily_stats_buckets_by_route: Dict[int, List[dict]],
                        included: List[dict]) -> None:
    for route_id, stats_list in daily_stats_by_route.items():
        r_hash = _route_hash(route_id)
        for s in stats_list:
            included.append(make_resource(
                'route_daily_stat', f"{r_hash}:{s['day']}",
                attributes={k: s.get(k) for k in
                            ('day', 'tx_count', 'swap_count', 'volume_usd', 'fees_usd')},
                relationships={
                    'route': rel_data('route', r_hash),
                    'daily_stats_bucket': rel_data_many(
                        [('route_daily_stat_bucket', f"{r_hash}:{s['day']}:{b['bucket_index']}")
                         for b in daily_stats_buckets_by_route.get(route_id, [])
                         if b['day'] == s['day']]),
                },
            ))


def _append_daily_stats_buckets(daily_stats_buckets_by_route: Dict[int, List[dict]],
                                included: List[dict]) -> None:
    for route_id, buckets in daily_stats_buckets_by_route.items():
        r_hash = _route_hash(route_id)
        for b in buckets:
            included.append(make_resource(
                'route_daily_stat_bucket', f"{r_hash}:{b['day']}:{b['bucket_index']}",
                attributes={k: b.get(k) for k in
                            ('day', 'bucket_index', 'tx_count', 'sample_count',
                             'volume_usd', 'fees_usd', 'log_sum', 'log_sum2')},
                relationships={'route': rel_data('route', r_hash)},
            ))


def _append_pool_daily_stats(daily_stats_by_pool: Dict[int, List[dict]],
                             daily_stats_buckets_by_pool: Dict[int, List[dict]],
                             included: List[dict]) -> None:
    for pool_id, stats_list in daily_stats_by_pool.items():
        for s in stats_list:
            included.append(make_resource(
                'pool_daily_stat', f"{pool_id}:{s['day']}",
                attributes={k: s.get(k) for k in
                            ('day', 'tx_count', 'volume_usd', 'tvl_usd')},
                relationships={
                    'pool': rel_data('pool', pool_id),
                    'daily_stats_bucket': rel_data_many(
                        [('pool_daily_stat_bucket', f"{pool_id}:{s['day']}:{b['bucket_index']}")
                         for b in daily_stats_buckets_by_pool.get(pool_id, [])
                         if b['day'] == s['day']]),
                },
            ))


def _append_pool_daily_stats_buckets(daily_stats_buckets_by_pool: Dict[int, List[dict]],
                                     included: List[dict]) -> None:
    for pool_id, buckets in daily_stats_buckets_by_pool.items():
        for b in buckets:
            included.append(make_resource(
                'pool_daily_stat_bucket', f"{pool_id}:{b['day']}:{b['bucket_index']}",
                attributes={k: b.get(k) for k in
                            ('day', 'bucket_index', 'tx_count', 'sample_count',
                             'volume_usd', 'fees_usd', 'log_sum', 'log_sum2')},
                relationships={'pool': rel_data('pool', pool_id)},
            ))


def _append_pools(pools: Dict[int, dict], included: List[dict]) -> None:
    for pool_id, p in pools.items():
        attrs = {
            'pool_address': p.get('pool_address'),
            'pool_id': p.get('v4_pool_id'),
            'fee_bps': p.get('fee_bps'),
            'fee_tier': p.get('fee_tier'),
            'protocol': p.get('protocol'),
            'canonical_address': p.get('canonical_address'),
            'defillama_uuid': p.get('defillama_uuid'),
            'tvl_usd': p.get('tvl_usd'),
            'volume_usd': p.get('volume_usd'),
            'volume_usd_24h': p.get('volume_usd_24h'),
            'tx_count': p.get('tx_count'),
            'created_at': p.get('created_at'),
            'links': p.get('links'),
            'history': p.get('history'),
        }
        included.append(make_resource(
            'pool', pool_id,
            attributes={k: v for k, v in attrs.items() if v is not None},
            relationships={
                'chain': rel_data('chain', p.get('chain_id')),
                'coin0': rel_data('coin', p.get('coin0_id')),
                'coin1': rel_data('coin', p.get('coin1_id')),
            },
        ))


def _append_coins(coins: Dict[int, dict], contracts_by_coin: Dict[int, List[dict]],
                  families_by_coin: Dict[int, List[str]], included: List[dict]) -> None:
    for coin_id, c in coins.items():
        included.append(make_resource(
            'coin', coin_id,
            attributes={k: c.get(k) for k in
                        ('symbol', 'name', 'slug', 'price', 'market_cap', 'cmc_rank',
                         'decimals', 'image_url', 'tvl', 'total_supply',
                         'circulating_supply', 'max_supply')},
            relationships={
                'contracts': rel_data_many([('coin_contract', f"{coin_id}:{cc['chain']}") for cc in contracts_by_coin.get(coin_id, [])]),
                'families': rel_data_many([('coin_family', f) for f in families_by_coin.get(coin_id, [])]),
            },
        ))


def _append_contracts(contracts_by_coin: Dict[int, List[dict]], included: List[dict]) -> None:
    for coin_id, ccs in contracts_by_coin.items():
        for cc in ccs:
            included.append(make_resource(
                'coin_contract', f"{coin_id}:{cc['chain']}",
                attributes={k: cc.get(k) for k in
                            ('chain', 'contract_address', 'decimals', 'is_native',
                             'tracked', 'source', 'confidence_score')},
                relationships={'coin': rel_data('coin', coin_id)},
            ))


def _append_families(families_by_coin: Dict[int, List[str]],
                     family_members: Dict[str, List[int]], included: List[dict]) -> None:
    seen: Set[str] = set()
    for coin_id, fams in families_by_coin.items():
        for fam in fams:
            if fam in seen:
                continue
            seen.add(fam)
            included.append(make_resource(
                'coin_family', fam,
                attributes={'name': fam},
                relationships={
                    'members': rel_data_many([('coin', mid) for mid in family_members.get(fam, [])]),
                },
            ))

