"""
Postgres Swap Data Fetcher

This module fetches swap data from the local Postgres database
for specified tokens within a given time range.
"""

import os
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from eth_hash.auto import keccak
from config import (
    DATA_WAREHOUSE_DB,
    ADDRESS_TO_SYMBOL
)

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is always present in our images
    yaml = None

# ---------------------------------------------------------------------------
# CREATE2 pool-address derivation
#
# For V2/V3-style DEXes the pool address is fully determined by
# (deployer, token pair, fee tier), so we derive it locally instead of trusting
# liquidity_pool.pool_address — many legacy rows there hold fabricated
# addresses that 404 on Uniswap / Revert / DexScreener.
#
# Factory addresses and init code hashes are read from config/dex-config.yaml,
# the same file api/main.py uses, so there is exactly one source of truth.
# _FALLBACK_DEX_CONFIG mirrors it for environments where it is not mounted.
# NEVER hand-edit these hashes: a single wrong nibble yields a plausible-looking
# address that points at nothing.
# ---------------------------------------------------------------------------
_UNISWAP_V3_INIT_HASH = '0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54'
_PANCAKE_V3_INIT_HASH = '0x6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2'
_UNISWAP_V2_INIT_HASH = '0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f'

_FALLBACK_DEX_CONFIG = {
    'uniswap_v3': {
        net: {'factory': factory, 'init_hash': _UNISWAP_V3_INIT_HASH}
        for net, factory in (
            ('ethereum', '0x1F98431c8aD98523631AE4a59f267346ea31F984'),
            ('arbitrum', '0x1F98431c8aD98523631AE4a59f267346ea31F984'),
            ('optimism', '0x1F98431c8aD98523631AE4a59f267346ea31F984'),
            ('polygon', '0x1F98431c8aD98523631AE4a59f267346ea31F984'),
            ('base', '0x33128a8fC17869897dcE68Ed026d694621f6FDfD'),
            ('bsc', '0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7'),
        )
    },
    # PancakeSwap V3 deploys pools from the PoolDeployer, not the Factory.
    'pancakeswap_v3': {
        net: {'factory': '0x41ff9AA7e16B8B1a8a8dc4f0eFacd93D02d071c9',
              'init_hash': _PANCAKE_V3_INIT_HASH}
        for net in ('bsc', 'ethereum', 'arbitrum', 'base')
    },
    'uniswap_v2': {
        'ethereum': {'factory': '0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f',
                     'init_hash': _UNISWAP_V2_INIT_HASH},
    },
}

_DEX_CONFIG: Optional[dict] = None


def _load_dex_config() -> dict:
    """Load config/dex-config.yaml once, falling back to the built-in table.

    Searched in the repo-relative location first, then the container mount
    points used by the API server and the Airflow images.
    """
    global _DEX_CONFIG
    if _DEX_CONFIG is not None:
        return _DEX_CONFIG

    loaded = None
    if yaml is not None:
        here = os.path.dirname(os.path.abspath(__file__))
        for path in (
            os.path.join(here, '..', '..', 'config', 'dex-config.yaml'),
            '/app/config/dex-config.yaml',
            '/opt/airflow/config/dex-config.yaml',
        ):
            try:
                with open(path, 'r') as fh:
                    candidate = yaml.safe_load(fh)
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(candidate, dict) and candidate:
                loaded = candidate
                break

    _DEX_CONFIG = loaded if loaded else _FALLBACK_DEX_CONFIG
    return _DEX_CONFIG


def _network_config_keys(net: str) -> Tuple[str, ...]:
    """Candidate config keys for a chain name, newest naming first.

    The DB stores 'BNB' where the config says 'bsc', and PancakeSwap's section
    keys Ethereum as 'eth' while Uniswap's says 'ethereum'.
    """
    n = (net or '').lower()
    if n in ('bnb', 'bsc', 'binance', 'binance smart chain'):
        return ('bsc', 'bnb', 'binance')
    if n in ('ethereum', 'eth', 'mainnet'):
        return ('ethereum', 'eth', 'mainnet')
    return (n,)


def _get_dex_params(proto_key: str, net: str) -> Optional[Tuple[str, str]]:
    """Return (deployer_address, init_code_hash) for a protocol/network, or None."""
    cfg = _load_dex_config().get(proto_key)
    if not isinstance(cfg, dict):
        return None
    if 'factory' in cfg and 'init_hash' in cfg:
        entry = cfg
    else:
        entry = next(
            (cfg[key] for key in _network_config_keys(net) if isinstance(cfg.get(key), dict)),
            None
        )
    if not isinstance(entry, dict):
        return None
    factory, init_hash = entry.get('factory'), entry.get('init_hash')
    if not factory or not init_hash:
        return None
    return factory, init_hash


def _to_checksum_address(addr_hex: str) -> str:
    addr = addr_hex.lower().removeprefix('0x')
    hash_hex = keccak(addr.encode('ascii')).hex()
    return '0x' + ''.join(
        c.upper() if int(hash_hex[i], 16) >= 8 else c
        for i, c in enumerate(addr)
    )


def _derive_canonical_address(proto: str, net: str, fee_bps: Optional[float],
                              addr0: Optional[str], addr1: Optional[str]) -> Optional[str]:
    """Derive a V2/V3 pool's CREATE2 address from its tokens and fee tier.

    Returns None whenever the pool is not derivable — a V4 singleton, an
    unconfigured protocol/network, a token with no known contract address, or a
    missing fee tier — so the caller keeps the stored address instead of
    linking to an address that does not exist.
    """
    if not addr0 or not addr1 or not addr0.startswith('0x') or not addr1.startswith('0x'):
        return None

    proto_key = (proto or '').lower().replace(' ', '_').replace('-', '_')
    params = _get_dex_params(proto_key, net or '')
    if not params:
        return None
    factory_hex, init_hash_hex = params
    is_v2 = proto_key.endswith('_v2')

    fee_val = 0
    if not is_v2:
        # fee_bps is basis points (5 = 0.05%, 30 = 0.30%); the fee baked into
        # the V3 pool salt is in hundredths of a bip, so it is bps * 100.
        if fee_bps is None:
            return None
        fee_val = int(round(float(fee_bps) * 100))
        if fee_val <= 0:
            return None

    try:
        t0_bytes = bytes.fromhex(addr0.removeprefix('0x'))
        t1_bytes = bytes.fromhex(addr1.removeprefix('0x'))
        tokens = sorted([t0_bytes, t1_bytes])
        if is_v2:
            salt = keccak(tokens[0] + tokens[1])
        else:
            salt_data = b'\x00' * 12 + tokens[0] + b'\x00' * 12 + tokens[1] + fee_val.to_bytes(32, 'big')
            salt = keccak(salt_data)

        f_bytes = bytes.fromhex(factory_hex.removeprefix('0x'))
        ih_bytes = bytes.fromhex(init_hash_hex.removeprefix('0x'))
        derived = keccak(b'\xff' + f_bytes + salt + ih_bytes)[12:].hex()
        return _to_checksum_address('0x' + derived)
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Module-level connection pool — shared across all PostgresFetcher instances
# and across fetch_swaps / fetch_pool_stats / fetch_latest_prices (and any
# caller that borrows get_conn()). Eliminates per-call TCP+auth handshake.
# ---------------------------------------------------------------------------
_POOL: Optional[ThreadedConnectionPool] = None
_POOL_MAXCONN = 8


def _get_pool() -> ThreadedConnectionPool:
    global _POOL
    if _POOL is None or _POOL.closed:
        _POOL = ThreadedConnectionPool(
            minconn=1, maxconn=_POOL_MAXCONN, dsn=DATA_WAREHOUSE_DB
        )
    return _POOL


@contextmanager
def get_conn():
    """Borrow a pooled connection and return it to the pool on exit.

    Read-only queries are rolled back (snapshot released) on success;
    errors are rolled back and re-raised so the connection returns clean.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.rollback()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


class PostgresFetcher:
    """Fetches swap data from local Postgres database"""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def _log(self, message: str):
        """Print log message if verbose mode is enabled"""
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [DB] {message}")

    @staticmethod
    def _build_token_clause(start_tokens: Optional[List[str]] = None,
                            end_tokens: Optional[List[str]] = None,
                            token_filter: Optional[List[str]] = None,
                            broad: bool = False):
        """Build the coin-symbol filter clause used by swap queries.

        Returns (where_sql, params). The clause filters on the *pool's* coin
        symbols (c0.symbol / c1.symbol), which is equivalent to filtering on
        individual swap direction since a pool's coin0/coin1 are fixed.

        `start_tokens`/`end_tokens` take precedence over `token_filter` when
        present. A '*' wildcard on either side means "no constraint on that side".

        When `broad` is True, both sides are concrete, and the clause is
        loosened to match any leg touching EITHER a start OR an end token
        (union, ANY-based) instead of only the strict start/end pair. The route
        analyzer needs this: a multi-hop route's intermediate legs (e.g.
        WBTC→USDC→RLUSD) live on pools that hold one side but not both, so a
        strict pair filter returns zero rows and the chain is never fetched.
        """
        if start_tokens and end_tokens and not broad:
            start_upper = [s.upper() for s in start_tokens]
            end_upper = [e.upper() for e in end_tokens]

            start_has_wildcard = '*' in start_upper
            end_has_wildcard = '*' in end_upper

            if start_has_wildcard and end_has_wildcard:
                return "", []
            elif start_has_wildcard:
                return ("UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)",
                        [end_upper, end_upper])
            elif end_has_wildcard:
                return ("UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)",
                        [start_upper, start_upper])
            else:
                return (("(UPPER(c0.symbol) = ANY(%s) AND UPPER(c1.symbol) = ANY(%s)) "
                         "OR (UPPER(c0.symbol) = ANY(%s) AND UPPER(c1.symbol) = ANY(%s))"),
                        [start_upper, end_upper, end_upper, start_upper])

        # Broad (multi-hop) mode: both sides concrete, but we want every leg
        # touching EITHER side so intermediate hops (which never contain both
        # start and end, e.g. USDC in WBTC→USDC→RLUSD) are still fetched. This
        # is a lazy super-set; RouteAnalyzer then discards txs that don't form a
        # real contiguous start→end chain.
        if broad and start_tokens and end_tokens:
            start_upper = [s.upper() for s in start_tokens if s != '*']
            end_upper = [e.upper() for e in end_tokens if e != '*']
            all_tokens = list(dict.fromkeys(start_upper + end_upper))
            if not all_tokens:
                return "", []
            return ("UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)",
                    [all_tokens, all_tokens])

        upper_symbols = [symbol.upper() for symbol in token_filter] if token_filter else None
        if token_filter and len(token_filter) == 2:
            t0, t1 = upper_symbols[0], upper_symbols[1]
            return (("(UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s) "
                     "OR (UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s)"),
                    [t0, t1, t1, t0])
        elif token_filter:
            return ("UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)",
                    [upper_symbols, upper_symbols])
        return "", []

    @staticmethod
    def _build_network_clause(network: Optional[str]):
        """Return (where_sql, param) for the network filter, or ('', None)."""
        if network and network.lower() != 'all':
            return " AND LOWER(ch.name) = LOWER(%s)", network
        return "", None
    
    def fetch_swaps(self, start_date: datetime, end_date: datetime,
                    token_filter: Optional[List[str]] = None,
                    network: Optional[str] = None,
                    start_tokens: Optional[List[str]] = None,
                    end_tokens: Optional[List[str]] = None,
                    broad: bool = False) -> List[Dict]:
        """
        Fetch all swap events for tracked tokens within the date range from Postgres.

        Queries the unified `swaps` table with coin_id joins for symbol resolution.

        When `broad` is True, legs touching EITHER start OR end token are fetched
        so multi-hop routes (which traverse intermediate tokens not on the queried
        pair) can be reconstructed. Use only for route analysis; pool-stats callers
        keep the strict direct-pair filter.
        """
        self._log(f"Fetching swaps from {start_date} to {end_date} (network={network}, tokens={token_filter}, start={start_tokens}, end={end_tokens}, broad={broad})")

        try:
            with get_conn() as conn:
                cur = conn.cursor()

                token_where, token_params = self._build_token_clause(start_tokens, end_tokens, token_filter, broad)
                network_where, network_param = self._build_network_clause(network)

                # 1. Pre-fetch matching pool IDs & metadata (typically 5-50 rows, finishes in < 10ms)
                pool_query = f"""
                    SELECT lp.id, ch.name AS network, pr.name AS protocol,
                           c0.symbol, c1.symbol, lp.fee_bps, lp.pool_address, lp.pool_id
                    FROM liquidity_pool lp
                    JOIN chain ch ON lp.chain_id = ch.id
                    JOIN protocol pr ON lp.protocol_id = pr.id
                    JOIN coin c0 ON lp.coin0_id = c0.coin_id
                    JOIN coin c1 ON lp.coin1_id = c1.coin_id
                """
                pool_params = []
                where_clauses = []
                if token_where:
                    where_clauses.append(f"({token_where})")
                    pool_params.extend(token_params)
                if network_param:
                    where_clauses.append(network_where.lstrip(" AND "))
                    pool_params.append(network_param)
                if where_clauses:
                    pool_query += " WHERE " + " AND ".join(where_clauses)

                cur.execute(pool_query, pool_params)
                pool_rows = cur.fetchall()

                if not pool_rows:
                    cur.close()
                    self._log("Fetch complete. No matching pools found.")
                    return []

                pool_meta = {}
                pool_ids = []
                for p_id, p_net, p_prot, c0_sym, c1_sym, p_fee, p_addr, p_pool_id in pool_rows:
                    pool_ids.append(p_id)
                    fee_str = 'Dynamic' if p_fee is None else f"{p_fee / 100.0:.2f}%"
                    pool_meta[p_id] = {
                        'network': p_net,
                        'protocol': p_prot,
                        'symbol0': c0_sym,
                        'symbol1': c1_sym,
                        'fee_display': fee_str,
                        'fee_bps': float(p_fee) if p_fee is not None else None,
                        'pool_address': p_addr or p_pool_id or '',
                        'pool_id': p_pool_id or p_addr or '',
                    }

                # 2. Query swaps table
                if broad and start_tokens and end_tokens and '*' not in start_tokens and '*' not in end_tokens:
                    start_upper = set(s.upper() for s in start_tokens)
                    end_upper = set(e.upper() for e in end_tokens)
                    
                    start_pool_ids = []
                    end_pool_ids = []
                    direct_pool_ids = []
                    
                    for p_id, p_net, p_prot, c0_sym, c1_sym, p_fee, p_addr, p_pool_id in pool_rows:
                        c0_u, c1_u = c0_sym.upper(), c1_sym.upper()
                        is_start = (c0_u in start_upper or c1_u in start_upper)
                        is_end = (c0_u in end_upper or c1_u in end_upper)
                        
                        if is_start: start_pool_ids.append(p_id)
                        if is_end: end_pool_ids.append(p_id)
                        if is_start and is_end: direct_pool_ids.append(p_id)

                    swaps_query = """
                        WITH direct_txs AS (
                            SELECT DISTINCT tx_hash FROM swaps WHERE pool_id = ANY(%s) AND ts >= %s AND ts <= %s
                        ),
                        start_txs AS (
                            SELECT DISTINCT tx_hash FROM swaps WHERE pool_id = ANY(%s) AND ts >= %s AND ts <= %s
                        ),
                        end_txs AS (
                            SELECT DISTINCT tx_hash FROM swaps WHERE pool_id = ANY(%s) AND ts >= %s AND ts <= %s
                        ),
                        candidate_txs AS (
                            SELECT tx_hash FROM direct_txs
                            UNION
                            SELECT s.tx_hash FROM start_txs s INTERSECT SELECT e.tx_hash FROM end_txs e
                        )
                        SELECT s.tx_hash, s.log_index, s.ts, s.pool_id, s.amount0, s.amount1, s.amount_usd
                        FROM swaps s
                        JOIN candidate_txs c ON s.tx_hash = c.tx_hash
                        WHERE s.ts >= %s AND s.ts <= %s AND (s.amount_usd >= 10.0 OR s.amount_usd = 0 OR s.amount_usd IS NULL)
                        ORDER BY s.tx_hash, s.log_index
                    """
                    cur.execute(swaps_query, [
                        direct_pool_ids, start_date, end_date,
                        start_pool_ids, start_date, end_date,
                        end_pool_ids, start_date, end_date,
                        start_date, end_date
                    ])
                else:
                    swaps_query = """
                        SELECT s.tx_hash, s.log_index, s.ts, s.pool_id, s.amount0, s.amount1, s.amount_usd
                        FROM swaps s
                        WHERE s.pool_id = ANY(%s)
                          AND s.ts >= %s AND s.ts <= %s
                          AND (s.amount_usd >= 10.0 OR s.amount_usd = 0 OR s.amount_usd IS NULL)
                        ORDER BY s.tx_hash, s.log_index
                    """
                    cur.execute(swaps_query, [pool_ids, start_date, end_date])
                rows = cur.fetchall()

                swaps = []
                for row in rows:
                    tx_hash = row[0]
                    log_index = row[1]
                    pid = row[3]
                    pm = pool_meta.get(pid)
                    if not pm:
                        continue
                    swaps.append({
                        'id': f"{tx_hash}#{log_index}",
                        'timestamp': int(row[2].timestamp()),
                        'tx_hash': tx_hash,
                        'token0_symbol': pm['symbol0'],
                        'token1_symbol': pm['symbol1'],
                        'amount0': float(row[4]) if row[4] is not None else 0.0,
                        'amount1': float(row[5]) if row[5] is not None else 0.0,
                        'amountUSD': float(row[6]) if row[6] is not None else 0.0,
                        'amount_usd': float(row[6]) if row[6] is not None else 0.0,
                        'fee_tier': pm['fee_display'],
                        'fee_bps': pm['fee_bps'],
                        'protocol': pm['protocol'],
                        'network': pm['network'],
                        'cid': pid,
                        'pool_address': pm['pool_address'],
                        'pool_id': pm['pool_id'],
                        'log_index': log_index,
                    })

                cur.close()

            self._log(f"Fetch complete. Total swaps from DB: {len(swaps)}")
            return swaps

        except Exception as e:
            self._log(f"Database query failed: {e}")
            raise

    def fetch_route_stats(self, start_date: datetime, end_date: datetime,
                          start_tokens: Optional[List[str]] = None,
                          end_tokens: Optional[List[str]] = None,
                          network: Optional[str] = None,
                          direction: str = 'forward') -> Dict:
        """Fast-path route analytics from the pre-aggregated route_daily_stats.

        Groups route_daily_stats by route_id (the specific pool topology) and
        returns the same shape RouteAnalyzer.get_results() produced, so the
        /api/routes/analyze enrichment pipeline runs unchanged on top of it:

            {'total_tx': int, 'total_volume': float, 'routes': [{
                'path': str, 'path_tokens': [sym, fee|protocol|network, ...],
                'count', 'swaps', 'volume', 'market_size', 'avg_volume',
                'pct_volume', 'hops', 'last_activity', 'direction'
            }, ...]}

        `direction`: 'forward' (origin token in start_tokens, dest in
        end_tokens), 'reverse' (roles swapped), or 'both' (union of the two;
        each returned route carries this direction).

        Because stats are summed per (route_id, day), this replaces the
        ~100k-swap-per-day fetch + Python topology pass for long windows with a
        handful of summed rows per route (measured ~100-1000x speedup).
        """
        self._log(f"Route stats {start_date} -> {end_date} (start={start_tokens}, end={end_tokens}, network={network}, dir={direction})")

        start_syms = [s.upper() for s in (start_tokens or [])]
        end_syms = [e.upper() for e in (end_tokens or [])]
        start_wild = '*' in start_syms
        end_wild = '*' in end_syms
        dir_norm = (direction or 'forward').lower()
        if dir_norm not in ('forward', 'reverse', 'both'):
            dir_norm = 'forward'

        def _side_pred(col: str, syms: List[str], wild: bool) -> tuple:
            """Return (sql_fragment, [params]) for one side of a pair predicate."""
            if wild or not syms:
                return "(1=1)", []
            return f"UPPER({col}) = ANY(%s)", [syms]

        fwd_a, fwd_p1 = _side_pred('pair.origin_symbol', start_syms, start_wild)
        fwd_b, fwd_p2 = _side_pred('pair.dest_symbol', end_syms, end_wild)
        pair_sql = f"({fwd_a} AND {fwd_b})"
        pair_params = fwd_p1 + fwd_p2

        try:
            with get_conn() as conn:
                cur = conn.cursor()

                sql_params: List = [start_date, end_date] + pair_params
                query = f"""
                    SELECT
                        r.route_id,
                        pair.id AS pair_id,
                        pair.origin_symbol,
                        pair.dest_symbol,
                        r.hops,
                        SUM(rs.tx_count)   AS tx_count,
                        SUM(rs.swap_count) AS swap_count,
                        SUM(rs.volume_usd) AS volume_usd,
                        MAX(rs.day)        AS last_day
                    FROM route_daily_stats rs
                    JOIN route r ON rs.route_id = r.route_id
                    JOIN origin_destination_pair pair ON r.pair_id = pair.id
                    JOIN chain ch ON r.chain_id = ch.id
                    WHERE rs.day >= %s::date AND rs.day <= %s::date
                      AND {pair_sql}
                """
                if network and network.lower() != 'all':
                    query += " AND LOWER(ch.name) = LOWER(%s)"
                    sql_params.append(network)
                query += """
                    GROUP BY r.route_id, pair.id, pair.origin_symbol, pair.dest_symbol, r.hops
                    ORDER BY volume_usd DESC
                """
                cur.execute(query, sql_params)
                agg_rows = cur.fetchall()

                # Build per-route aggregates.
                route_records = []
                for (route_id, pair_id, origin_sym, dest_sym, hops,
                     tx_count, swap_count, volume_usd, last_day) in agg_rows:
                    route_records.append({
                        'route_id': route_id,
                        'pair_id': pair_id,
                        'origin_symbol': (origin_sym or '').upper(),
                        'dest_symbol': (dest_sym or '').upper(),
                        'hops': int(hops or 0),
                        'tx_count': int(tx_count or 0),
                        'swap_count': int(swap_count or 0),
                        'volume_usd': float(volume_usd or 0),
                        'last_day': last_day,
                    })

                # Fetch hop topology for the matched routes to rebuild
                # path_tokens ([Token, fee|protocol|network, Token, ...]).
                hops_by_route: Dict[int, List] = {}
                if route_records:
                    route_ids = [r['route_id'] for r in route_records]
                    cur.execute(
                        """
                        SELECT
                            h.route_id,
                            h.seq,
                            UPPER(ci.symbol) AS token_in_sym,
                            UPPER(co.symbol) AS token_out_sym,
                            CASE WHEN lp.fee_bps IS NULL THEN 'Dynamic'
                                 ELSE (lp.fee_bps / 100.0)::text || '%%' END AS fee_display,
                            pr.name AS protocol,
                            ch.name AS network
                        FROM route_hop h
                        JOIN liquidity_pool lp ON h.pool_id = lp.id
                        JOIN protocol pr ON lp.protocol_id = pr.id
                        JOIN chain ch ON lp.chain_id = ch.id
                        LEFT JOIN coin_contract cic ON LOWER(cic.contract_address) = LOWER(h.token_in) AND cic.chain_id = lp.chain_id
                        LEFT JOIN coin ci ON cic.coin_id = ci.coin_id
                        LEFT JOIN coin_contract coc ON LOWER(coc.contract_address) = LOWER(h.token_out) AND coc.chain_id = lp.chain_id
                        LEFT JOIN coin co ON coc.coin_id = co.coin_id
                        WHERE h.route_id = ANY(%s)
                        ORDER BY h.route_id, h.seq
                        """,
                        (route_ids,),
                    )
                    for (route_id, seq, token_in_sym, token_out_sym, fee_display,
                         protocol, network) in cur.fetchall():
                        hops_by_route.setdefault(route_id, []).append({
                            'seq': seq,
                            'token_in_sym': token_in_sym,
                            'token_out_sym': token_out_sym,
                            'fee': fee_display,
                            'protocol': protocol,
                            'network': network,
                        })

                results = []
                total_volume = sum(r['volume_usd'] for r in route_records)
                for rec in route_records:
                    ordered = sorted(hops_by_route.get(rec['route_id'], []),
                                     key=lambda hp: hp['seq'])
                    # Rebuild [Token, Fee, Token, Fee, ...]: hop i connects
                    # token_in -> token_out over its pool. Because chains are
                    # contiguous, hop i+1's token_in equals hop i's token_out,
                    # so each hop contributes its fee node + the output token.
                    path_tokens = []
                    for i, hp in enumerate(ordered):
                        if i == 0:
                            path_tokens.append(hp['token_in_sym'] or rec['origin_symbol'] or '')
                        path_tokens.append(f"{hp['fee']}|{hp['protocol']}|{hp['network']}")
                        path_tokens.append(hp['token_out_sym'] or rec['dest_symbol'] or '')
                    if not path_tokens:
                        path_tokens = [rec['origin_symbol'] or '', rec['dest_symbol'] or '']

                    cum_fee = 0.0
                    for i in range(1, len(path_tokens), 2):
                        fee_str = path_tokens[i]
                        try:
                            fee_raw = fee_str.split('|')[0].strip().replace('%', '')
                            if fee_raw.lower() == 'dynamic':
                                cum_fee += 0.0002
                            elif fee_raw:
                                val = float(fee_raw)
                                cum_fee += (val / 1_000_000.0 if val > 5 else val / 100.0)
                        except (ValueError, AttributeError):
                            pass

                    vol = rec['volume_usd']
                    tc = rec['tx_count']
                    results.append({
                        'path': ' --> '.join(path_tokens),
                        'path_tokens': path_tokens,
                        'route_id': rec['route_id'],
                        'pair_id': rec['pair_id'],
                        'count': tc,
                        'swaps': rec['swap_count'],
                        'volume': vol,
                        'market_size': vol * cum_fee,
                        'avg_volume': vol / tc if tc else 0,
                        'pct_volume': (vol / total_volume * 100) if total_volume else 0,
                        'hops': rec['hops'],
                        'last_activity': rec['last_day'].isoformat() if rec['last_day'] else None,
                        'direction': direction,
                    })

                results.sort(key=lambda r: r['volume'], reverse=True)

                return {
                    'routes': results,
                    'total_tx': sum(rec['tx_count'] for rec in route_records),
                    'total_volume': total_volume,
                }

        except Exception as e:
            self._log(f"Route stats query failed: {e}")
            raise

    def fetch_pool_stats(self, pools: List[List[str]], start_date: datetime, end_date: datetime, prices: Optional[Dict[str, float]] = None, tvl_mode: str = 'avg', use_swaps_fallback: bool = False) -> Dict[str, Dict[str, float]]:
        """
        Fetch stats (APR) for a list of pools [(t0, t1, fee), ...] within date range.
        Returns dict: { "T0-T1-FEE": apr_float }

        tvl_mode: 'avg'   -> TVL is the row-count-weighted average over the range
                            (with a latest-snapshot fallback when the range has no
                            non-zero TVL), matching the old per-pool AVG behavior.
                  'latest' -> TVL is the most recent non-zero snapshot in the DB
                            (across all history), i.e. the pool's current size.

        use_swaps_fallback: when True, pools still at zero volume after the
            liquidity_pool_history aggregation fall back to the raw swaps table
            for volume. This is intentionally opt-in: only the counterfactual
            tools (/api/routes/undercut, /api/sps/find) pass it, because they
            are exempt from the no-raw-swaps rule; the main analytics endpoints
            never read the swaps tables.
        

        Implementation note: this used to build one UNION ALL subquery *per pool*
        (plus a correlated TVL-fallback subquery per pool) and join
        liquidity_pool_history -> liquidity_pool on symbol pairs every time. That
        made latency grow linearly with the number of pools and defeated the
        symbol-pair indexes. The current shape resolves every requested pool to
        its pool_id(s) in ONE query, then runs ONE grouped aggregation over
        liquidity_pool_history keyed by pool_id, with ONE batched TVL-fallback
        query for pools that had no non-zero TVL in range.
        """
        if not pools:
            return {}

        try:
            conn = _get_pool().getconn()
            cur = conn.cursor()

            results = {}
            pool_meta = {}

            # Helper to parse fee string to basis points or float
            def is_fee_match(lp_fee_bps, fee_raw_str):
                if lp_fee_bps is None:
                    return fee_raw_str == 'Dynamic' or not fee_raw_str
                raw = str(fee_raw_str)
                clean = raw.split('|')[0].replace('%', '').strip()
                try:
                    val = float(clean)
                except Exception:
                    return False
                if '%' in raw:
                    # Percentage string ("0.01%", "1%", "0.3%") -> bps = val*100.
                    # Never fall back to the raw number: that would conflate a
                    # 0.01% (1 bps) pool with a 1% (100 bps) pool.
                    return abs(lp_fee_bps - val * 100.0) < 1e-4
                # Bare numeric: accept raw bps or its percent form.
                return any(abs(lp_fee_bps - c) < 1e-4 for c in (val, val * 100.0))

            # ------------------------------------------------------------------
            # Phase 0: normalize every requested pool into pool_meta[key].
            # ------------------------------------------------------------------
            all_networks = set()
            all_protocols = set()
            all_symbols = set()
            pair_index = {}

            for p in pools:
                t0, t1, fee_raw_full = p
                t0_sym, t1_sym = t0.upper(), t1.upper()

                network = "Ethereum"
                parts = str(fee_raw_full).split('|')
                if len(parts) >= 3:
                    network = parts[2].strip()
                
                network_lower = network.lower()

                protocol = "Uniswap V3"
                if len(parts) >= 2:
                    proto_raw = parts[1].strip()
                    if proto_raw.lower() in ('v3', 'uniswap v3'):
                        protocol = 'Uniswap V3'
                    elif proto_raw.lower() in ('v4', 'uniswap v4'):
                        protocol = 'Uniswap V4'
                    elif proto_raw.lower() in ('pancakeswap v4', 'pancake v4',
                                              'pancakeswap-v4', 'pancake-v4'):
                        protocol = 'PancakeSwap V4'
                    else:
                        protocol = proto_raw

                key = f"{t0}-{t1}-{fee_raw_full}"
                pool_meta[key] = {
                    't0_sym': t0_sym, 't1_sym': t1_sym, 'network': network_lower,
                    'protocol': protocol, 'fee_raw_full': fee_raw_full,
                    'pool_ids': [], 'total_vol': 0.0, 'avg_tvl': 0.0, 'latest_tvl': 0.0,
                }

                all_networks.add(network_lower)
                all_protocols.add(protocol)
                all_symbols.add(t0_sym)
                all_symbols.add(t1_sym)
                pair_index.setdefault((network_lower, protocol, frozenset((t0_sym, t1_sym))), []).append(key)

            # Get chain and protocol ID lookup maps
            cur.execute("SELECT id, name FROM chain")
            chain_map = {row[1].lower(): row[0] for row in cur.fetchall()}
            cur.execute("SELECT id, name FROM protocol")
            protocol_map = {row[1].lower(): row[0] for row in cur.fetchall()}

            # ------------------------------------------------------------------
            # Phase 1: resolve pool_id(s) for ALL requested pools in ONE query.
            # ------------------------------------------------------------------
            cur.execute("""
                SELECT lp.id, ch.name AS network, pr.name AS protocol, lp.fee_bps,
                       UPPER(c0.symbol), UPPER(c1.symbol)
                FROM liquidity_pool lp
                JOIN chain ch ON lp.chain_id = ch.id
                JOIN protocol pr ON lp.protocol_id = pr.id
                JOIN coin c0 ON lp.coin0_id = c0.coin_id
                JOIN coin c1 ON lp.coin1_id = c1.coin_id
                WHERE LOWER(ch.name) = ANY(%s)
                  AND pr.name = ANY(%s)
                  AND UPPER(c0.symbol) = ANY(%s)
                  AND UPPER(c1.symbol) = ANY(%s)
            """, (
                list(all_networks), list(all_protocols),
                list(all_symbols), list(all_symbols),
            ))
            for pid, net, proto, lp_fee_bps, c0, c1 in cur.fetchall():
                if c0 is None or c1 is None:
                    continue
                candidates = pair_index.get((net.lower(), proto, frozenset((c0, c1))))
                if not candidates:
                    continue
                for k in candidates:
                    meta = pool_meta[k]
                    if is_fee_match(lp_fee_bps, meta['fee_raw_full']):
                        if (meta['t0_sym'], meta['t1_sym']) in ((c0, c1), (c1, c0)):
                            meta['pool_ids'].append(pid)

            all_pool_ids = sorted({pid for m in pool_meta.values() for pid in m['pool_ids']})

            # ------------------------------------------------------------------
            # Phase 2: ONE grouped aggregation over liquidity_pool_history keyed
            # by pool_id. COUNT(*) FILTER lets us reconstruct the exact row-count-
            # weighted AVG(ABS(tvl_usd)) across all pools sharing a key, which is
            # what the old per-pool AVG computed.
            # ------------------------------------------------------------------
            # key -> [sum_vol, sum_tvl_weighted, sum_rows] for combining pool_ids
            key_accum = {k: [0.0, 0.0, 0] for k in pool_meta}
            pid_to_keys = {}
            for k, m in pool_meta.items():
                for pid in m['pool_ids']:
                    pid_to_keys.setdefault(pid, []).append(k)

            if all_pool_ids:
                cur.execute("""
                    SELECT pool_id,
                           COALESCE(SUM(ABS(volume_usd)), 0) AS total_vol,
                           AVG(ABS(tvl_usd)) FILTER (WHERE tvl_usd <> 0) AS avg_tvl,
                           COUNT(*) FILTER (WHERE tvl_usd <> 0) AS n_rows
                    FROM liquidity_pool_history
                    WHERE pool_id = ANY(%s)
                      AND date >= %s::date AND date <= %s::date
                    GROUP BY pool_id
                """, (all_pool_ids, start_date, end_date))
                for pid, total_vol, avg_tvl, n_rows in cur.fetchall():
                    n = int(n_rows or 0)
                    vol = float(total_vol or 0)
                    tvl = float(avg_tvl) if avg_tvl is not None else 0.0
                    for k in pid_to_keys.get(pid, ()):
                        key_accum[k][0] += vol
                        if n > 0:
                            key_accum[k][1] += tvl * n
                            key_accum[k][2] += n

                for k, (vol, weighted, n) in key_accum.items():
                    pool_meta[k]['total_vol'] = vol
                    pool_meta[k]['avg_tvl'] = (weighted / n) if n > 0 else 0.0

            # ------------------------------------------------------------------
            # Phase 2b: latest non-zero TVL snapshot per pool_id. ONE batched
            # DISTINCT ON query across every requested pool_id. In 'avg' mode this
            # is used only as a fallback for keys with no non-zero TVL in range
            # (picking the most recent non-zero TVL per pool_id; per key we take
            # the latest by date across its pool_ids, matching the old LIMIT-1
            # intent). In 'latest' mode the snapshot IS the reported TVL.
            # ------------------------------------------------------------------
            latest_tvl = {}
            if all_pool_ids:
                cur.execute("""
                    SELECT DISTINCT ON (pool_id) pool_id, ABS(tvl_usd) AS tvl, date
                    FROM liquidity_pool_history
                    WHERE pool_id = ANY(%s) AND tvl_usd <> 0
                    ORDER BY pool_id, date DESC
                """, (all_pool_ids,))
                for pid, tvl, dt in cur.fetchall():
                    latest_tvl[pid] = (dt, float(tvl or 0))

            for k in pool_meta:
                best_date = None
                best_tvl = 0.0
                for pid in pool_meta[k]['pool_ids']:
                    fb = latest_tvl.get(pid)
                    if not fb:
                        continue
                    fb_date, fb_tvl = fb
                    if fb_tvl <= 0:
                        continue
                    if best_date is None or (fb_date is not None and (best_date is None or fb_date > best_date)):
                        best_date = fb_date
                        best_tvl = fb_tvl
                if best_tvl > 0:
                    pool_meta[k]['latest_tvl'] = best_tvl
                    if tvl_mode == 'latest' or (tvl_mode == 'avg' and pool_meta[k]['avg_tvl'] <= 1.0):
                        pool_meta[k]['avg_tvl'] = best_tvl

            # ------------------------------------------------------------------
            # Phase 3: volume fallback from the raw swaps table. Opt-in only:
            # the counterfactual tools (/api/routes/undercut, /api/sps/find) are
            # exempt from the no-raw-swaps rule and rely on this to price pools
            # that have no liquidity_pool_history rows yet. The main analytics
            # endpoints never set use_swaps_fallback, so they stay raw-free.
            # Each key gets its own tightly-scoped subquery (one exact symbol
            # pair, one network/protocol, two fee-tier forms) so the planner
            # drives off the (network, timestamp) covering index and only
            # touches a small row set. Subqueries are UNION ALL'd in batches of
            # 20 — one round-trip per batch.
            # ------------------------------------------------------------------
            if use_swaps_fallback:
                pool_queries_swaps = []
                params_swaps = []
                for k, meta in pool_meta.items():
                    if meta.get('total_vol', 0) == 0 and meta['pool_ids']:
                        pool_queries_swaps.append("""
                        SELECT %s, c0.symbol, c1.symbol, SUM(s.amount_usd), SUM(ABS(s.amount0)), SUM(ABS(s.amount1))
                        FROM swaps s
                        JOIN liquidity_pool lp ON s.pool_id = lp.id
                        JOIN coin c0 ON lp.coin0_id = c0.coin_id
                        JOIN coin c1 ON lp.coin1_id = c1.coin_id
                        WHERE s.ts >= %s AND s.ts <= %s AND s.pool_id = ANY(%s)
                        GROUP BY c0.symbol, c1.symbol
                        """)
                        params_swaps.extend([k, start_date, end_date, meta['pool_ids']])

                if pool_queries_swaps:
                    batch_size = 20
                    for i in range(0, len(pool_queries_swaps), batch_size):
                        batch_queries = pool_queries_swaps[i:i+batch_size]
                        batch_params = params_swaps[i*4:(i+batch_size)*4]
                        cur.execute(" UNION ALL ".join(batch_queries), tuple(batch_params))
                        for row in cur.fetchall():
                            k = row[0]
                            usd_sum = float(row[3] or 0)
                            if usd_sum > 0:
                                pool_meta[k]['total_vol'] = pool_meta[k].get('total_vol', 0) + usd_sum
                            elif prices is not None:
                                p0 = prices.get(row[1]) or (1.0 if any(x in row[1].upper() for x in ['USD','EUR']) else 0)
                                p1 = prices.get(row[2]) or (1.0 if any(x in row[2].upper() for x in ['USD','EUR']) else 0)
                                v0 = float(row[4] or 0)
                                if v0 > 1e12:
                                    v0 /= 1e18
                                elif any(b in row[1].upper() for b in ['BTC', 'WBTC', 'BTCB']) and v0 > 1e4:
                                    v0 /= 1e8

                                v1 = float(row[5] or 0)
                                if v1 > 1e12:
                                    v1 /= 1e18
                                elif any(b in row[2].upper() for b in ['BTC', 'WBTC', 'BTCB']) and v1 > 1e4:
                                    v1 /= 1e8
                                pool_meta[k]['total_vol'] = pool_meta[k].get('total_vol', 0) + (v0*p0 + v1*p1)/2.0

            # Calculate APR
            for k, meta in pool_meta.items():
                avg_tvl = meta.get('avg_tvl', 0)
                total_vol = meta.get('total_vol', 0)
                t0_sym, t1_sym = meta['t0_sym'], meta['t1_sym']

                days = max(1, (end_date - start_date).days)
                # If TVL is missing, zero, or unreasonably low (e.g. less than 5% of average daily volume),
                # we do not calculate APR (we set it to None, which displays as a dash '-' or 'N/A' in the UI).
                is_unreliable_tvl = avg_tvl <= 1.0 or (total_vol > 0.0 and avg_tvl < (total_vol / days) * 0.05)

                # Calculate fee rate
                fee_rate = None
                try:
                    fee_str = str(meta['fee_raw_full']).split('|')[0].strip()
                    if fee_str == 'Dynamic' or not fee_str:
                        fee_rate = 0.0002
                    else:
                        val = float(fee_str.replace('%', '').strip())
                        if val > 0:
                            if val >= 10.0 and val.is_integer():
                                fee_rate = val / 1000000.0
                            else:
                                fee_rate = val / 100.0
                        else:
                            fee_rate = 0.0
                except Exception:
                    fee_rate = 0.003

                apr = None
                if fee_rate == 0.0:
                    apr = 0.0
                elif fee_rate is not None and not is_unreliable_tvl:
                    try:
                        fees_earned = total_vol * fee_rate
                        apr = (fees_earned / avg_tvl) * (365.0 / days)
                    except:
                        pass

                pool_stat = {'apr': apr, 'tvl': meta.get('avg_tvl', 0.0), 'volume': meta.get('total_vol', 0.0)}
                results[k] = pool_stat
                # Reverse-token-order key (preserves the old behavior without
                # the k.split('-') bug that broke on fees containing '-').
                t0, t1, f = k.split('-', 2)
                results[f"{t1}-{t0}-{f}"] = pool_stat

            cur.close()
            try:
                conn.rollback()
            except Exception:
                pass
            _get_pool().putconn(conn)
            return results

        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                _get_pool().putconn(conn)
            except Exception:
                pass
            self._log(f"APR fetch failed: {e}")
            return {}

    def fetch_latest_prices(self, symbols: Optional[List[str]] = None) -> Dict[str, float]:
        """
        Fetch the most recent price per symbol from coin_price_history.

        When `symbols` is provided, only those symbols are fetched (avoids a
        full scan of coin_price_history for every request). When omitted
        (e.g. ShortcutFinder), prices for every symbol are returned.
        Returns dict: { "SYMBOL": price_float }
        """
        try:
            with get_conn() as conn:
                cur = conn.cursor()

                if symbols:
                    upper_symbols = [s.upper() for s in symbols]
                    query = """
                        SELECT DISTINCT ON (c.symbol) c.symbol, h.price
                        FROM coin_price_history h
                        JOIN coin c ON h.coin_id = c.coin_id
                        WHERE c.symbol = ANY(%s)
                        ORDER BY c.symbol, h.timestamp DESC
                    """
                    cur.execute(query, (upper_symbols,))
                else:
                    query = """
                        SELECT DISTINCT ON (c.symbol) c.symbol, h.price
                        FROM coin_price_history h
                        JOIN coin c ON h.coin_id = c.coin_id
                        ORDER BY c.symbol, h.timestamp DESC
                    """
                    cur.execute(query)
                rows = cur.fetchall()

                prices = {row[0].upper(): float(row[1]) for row in rows if row[1] is not None}

                cur.close()
            return prices
        except Exception as e:
            self._log(f"Latest price fetch failed: {e}")
            return {}

    def fetch_pool_explorer_data(self, start_date: datetime, end_date: datetime,
                                  start_tokens: Optional[List[str]] = None,
                                  end_tokens: Optional[List[str]] = None,
                                  network: Optional[str] = None,
                                  limit: int = 0,
                                  offset: int = 0,
                                  sort_by: str = "volume") -> List[Dict]:
        """
        Fetch aggregated pool statistics directly from liquidity_pool_history & liquidity_pool.
        Sub-second execution that avoids scanning raw swaps.

        Args:
            limit: Max rows (0 = unlimited, for backward compat).
            offset: Rows to skip.
            sort_by: "volume" (default), "tvl", "tx_count", or "cid".
        """
        start_tokens_list = [t.upper() for t in (start_tokens or [])]
        end_tokens_list = [t.upper() for t in (end_tokens or [])]
        
        token_where, token_params = self._build_token_clause(start_tokens_list, end_tokens_list, None)
        network_where, network_param = self._build_network_clause(network)

        query = f"""
            SELECT 
                lp.id AS cid,
                COALESCE(lp.pool_address, '') AS pool_address,
                COALESCE((SELECT token_id FROM liquidity_pool_position lpp WHERE lpp.pool_id = lp.id AND lpp.token_id IS NOT NULL LIMIT 1), lp.pool_id, '') AS pool_id,
                ch.name AS network,
                pr.name AS protocol,
                c0.coin_id AS coin0_id,
                c0.symbol AS token0,
                c1.coin_id AS coin1_id,
                c1.symbol AS token1,
                COALESCE(c0.hardness, 0) AS h0,
                COALESCE(c1.hardness, 0) AS h1,
                lp.fee_bps,
                CASE
                    WHEN lp.fee_bps IS NULL THEN 'Dynamic'
                    ELSE (lp.fee_bps / 100.0)::text || '%%'
                END AS fee_display,
                COALESCE(SUM(lph.tx_count), 0) AS total_tx,
                COALESCE(SUM(ABS(lph.volume_usd)), 0) AS total_vol,
                 COALESCE(
                     AVG(ABS(lph.tvl_usd)) FILTER (WHERE lph.tvl_usd <> 0),
                     lt.tvl_usd,
                     0.0
                 ) AS avg_tvl,
                 MAX(lph.date) FILTER (WHERE lph.volume_usd <> 0) AS last_activity,
                 cc0.contract_address AS addr0,
                 cc1.contract_address AS addr1,
                 lp.created_at
            FROM liquidity_pool lp
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            JOIN coin c0 ON lp.coin0_id = c0.coin_id
            JOIN coin c1 ON lp.coin1_id = c1.coin_id
            JOIN liquidity_pool_history lph ON lph.pool_id = lp.id
            LEFT JOIN coin_contract cc0 ON cc0.coin_id = lp.coin0_id AND cc0.chain_id = lp.chain_id
            LEFT JOIN coin_contract cc1 ON cc1.coin_id = lp.coin1_id AND cc1.chain_id = lp.chain_id
            LEFT JOIN LATERAL (
                SELECT lph2.tvl_usd
                FROM liquidity_pool_history lph2
                WHERE lph2.pool_id = lp.id
                  AND lph2.tvl_usd IS NOT NULL
                  AND lph2.tvl_usd > 0
                ORDER BY lph2.date DESC
                LIMIT 1
            ) lt ON TRUE
            WHERE lph.date >= %s::date AND lph.date <= %s::date
        """
        params = [start_date, end_date]
        if token_where:
            query += f" AND ({token_where})"
            params.extend(token_params)
        if network_param:
            query += network_where
            params.append(network_param)

        # Validate and map sort_by to a SQL ORDER BY expression.
        sort_map = {
            "volume": "total_vol DESC",
            "tvl": "avg_tvl DESC NULLS LAST",
            "tx_count": "total_tx DESC",
            "cid": "lp.id ASC",
        }
        order_clause = sort_map.get(sort_by, "total_vol DESC")

        query += f"""
            GROUP BY lp.id, lp.pool_address, lp.pool_id, ch.name, pr.name,
                     c0.coin_id, c0.symbol, c1.coin_id, c1.symbol,
                     c0.hardness, c1.hardness, lp.fee_bps, lp.created_at,
                     cc0.contract_address, cc1.contract_address,
                     lt.tvl_usd
            HAVING SUM(ABS(lph.volume_usd)) > 0
            ORDER BY {order_clause}
        """
        if limit > 0:
            query += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])

        results = []
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                rows = cur.fetchall()
                for r in rows:
                    p_addr = r[1] or r[2] or ''
                    p_id = r[2] or r[1] or ''
                    net_val = r[3]
                    proto_val = r[4]
                    fee_bps_val = r[11]
                    addr0_val = r[17]
                    addr1_val = r[18]

                    canonical = _derive_canonical_address(proto_val, net_val, fee_bps_val, addr0_val, addr1_val)
                    if canonical:
                        p_addr = canonical
                        p_id = canonical

                    results.append({
                        'cid': r[0],
                        'pool_address': p_addr,
                        'pool_id': p_id,
                        'network': net_val,
                        'protocol': proto_val,
                        'coin0_id': r[5],
                        'token0': r[6],
                        'coin1_id': r[7],
                        'token1': r[8],
                        'h0': r[9],
                        'h1': r[10],
                        'fee_bps': r[11],
                        'fee_display': r[12],
                        'tx_count': int(r[13] or 0),
                        'volume_usd': float(r[14] or 0.0),
                        'avg_tvl': float(r[15]) if r[15] is not None else 0.0,
                        'last_activity': r[16],
                        'addr0': r[17],
                        'addr1': r[18],
                        'created_at': r[19],
                    })
                cur.close()
        except Exception as e:
            self._log(f"Direct pool history fetch failed: {e}")
        return results
