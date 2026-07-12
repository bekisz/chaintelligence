"""
Postgres Swap Data Fetcher

This module fetches swap data from the local Postgres database
for specified tokens within a given time range.
"""

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Optional
from config import (
    DATA_WAREHOUSE_DB,
    ADDRESS_TO_SYMBOL
)

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
    
    def fetch_swaps(self, start_date: datetime, end_date: datetime,
                    token_filter: Optional[List[str]] = None,
                    network: Optional[str] = None,
                    start_tokens: Optional[List[str]] = None,
                    end_tokens: Optional[List[str]] = None) -> List[Dict]:
        """
        Fetch all swap events for tracked tokens within the date range from Postgres.

        Queries the unified `swaps` table with coin_id joins for symbol resolution.
        """
        self._log(f"Fetching swaps from {start_date} to {end_date} (network={network}, tokens={token_filter}, start={start_tokens}, end={end_tokens})")

        try:
            with get_conn() as conn:
                cur = conn.cursor()

                # Determine the token query condition
                if start_tokens and end_tokens:
                    start_upper = [s.upper() for s in start_tokens]
                    end_upper = [e.upper() for e in end_tokens]
                    
                    start_has_wildcard = '*' in start_upper
                    end_has_wildcard = '*' in end_upper
                    
                    if start_has_wildcard and end_has_wildcard:
                        token_where = ""
                        token_params = []
                    elif start_has_wildcard:
                        token_where = "UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)"
                        token_params = [end_upper, end_upper]
                    elif end_has_wildcard:
                        token_where = "UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)"
                        token_params = [start_upper, start_upper]
                    else:
                        token_where = "(UPPER(c0.symbol) = ANY(%s) AND UPPER(c1.symbol) = ANY(%s)) OR (UPPER(c0.symbol) = ANY(%s) AND UPPER(c1.symbol) = ANY(%s))"
                        token_params = [start_upper, end_upper, end_upper, start_upper]
                else:
                    upper_symbols = [symbol.upper() for symbol in token_filter] if token_filter else None

                    # Build the token filter condition using coin symbols
                    if token_filter and len(token_filter) == 2:
                        t0, t1 = upper_symbols[0], upper_symbols[1]
                        token_where = "(UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s) OR (UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s)"
                        token_params = [t0, t1, t1, t0]
                    elif token_filter:
                        token_where = "UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)"
                        token_params = [upper_symbols, upper_symbols]
                    else:
                        token_where = ""
                        token_params = []

                # Network filter
                network_where = ""
                network_param = None
                if network and network.lower() != 'all':
                    network_where = " AND s.network = %s"
                    network_param = network

                # Query the unified swaps table
                query = f"""
                    SELECT s.tx_hash, s.log_index, s.ts, s.network, s.protocol,
                           c0.symbol, c1.symbol,
                           s.amount0, s.amount1, s.amount_usd, s.fee_display, s.fee_bps
                    FROM swaps s
                    JOIN coin c0 ON s.t0_coin_id = c0.coin_id
                    JOIN coin c1 ON s.t1_coin_id = c1.coin_id
                    WHERE s.ts >= %s AND s.ts <= %s
                      AND s.amount_usd >= 10.0
                """
                params = [start_date, end_date]
                if token_where:
                    query += f" AND ({token_where})"
                    params.extend(token_params)
                if network_param:
                    query += network_where
                    params.append(network_param)
                query += "\nORDER BY s.ts"

                cur.execute(query, params)
                rows = cur.fetchall()

                swaps = []
                for row in rows:
                    tx_hash = row[0]
                    log_index = row[1]
                    swaps.append({
                        'id': f"{tx_hash}#{log_index}",
                        'timestamp': int(row[2].timestamp()),
                        'tx_hash': tx_hash,
                        'token0_symbol': row[5],
                        'token1_symbol': row[6],
                        'amount0': float(row[7]) if row[7] is not None else 0.0,
                        'amount1': float(row[8]) if row[8] is not None else 0.0,
                        'amountUSD': float(row[9]) if row[9] is not None else 0.0,
                        'fee_tier': row[10] or '',
                        'fee_bps': float(row[11]) if row[11] is not None else None,
                        'protocol': row[4],
                        'network': row[3],
                        'log_index': log_index,
                    })

                cur.close()

            self._log(f"Fetch complete. Total swaps from DB: {len(swaps)}")
            return swaps

        except Exception as e:
            self._log(f"Database query failed: {e}")
            raise

    def fetch_swaps_streaming(self, start_date: datetime, end_date: datetime,
                              token_filter: Optional[List[str]] = None,
                              network: Optional[str] = None,
                              batch_size: int = 5000):
        """Generator that yields batches of swap dicts using a server-side cursor.

        Each yield is a list of up to `batch_size` swap dicts, keeping Python heap
        memory bounded regardless of the total result set size.  The caller receives
        one connection (not pooled) that lives for the duration of the generator.

        Example usage:
            for batch in fetcher.fetch_swaps_streaming(...):
                analyzer.process_batch(batch, ...)
        """
        import psycopg2

        upper_symbols = [symbol.upper() for symbol in token_filter] if token_filter else None

        # Build token filter condition using coin symbols
        if token_filter and len(token_filter) == 2:
            t0, t1 = upper_symbols[0], upper_symbols[1]
            token_where = "(UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s) OR (UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s)"
            token_params = [t0, t1, t1, t0]
        elif token_filter:
            token_where = "UPPER(c0.symbol) = ANY(%s) OR UPPER(c1.symbol) = ANY(%s)"
            token_params = [upper_symbols, upper_symbols]
        else:
            token_where = ""
            token_params = []

        network_where = ""
        network_param = None
        if network and network.lower() != 'all':
            network_where = " AND s.network = %s"
            network_param = network

        # Single query against the unified swaps table
        query = f"""
            SELECT s.tx_hash, s.log_index, s.ts, s.network, s.protocol,
                   c0.symbol, c1.symbol,
                   s.amount0, s.amount1, s.amount_usd, s.fee_display
            FROM swaps s
            JOIN coin c0 ON s.t0_coin_id = c0.coin_id
            JOIN coin c1 ON s.t1_coin_id = c1.coin_id
            WHERE s.ts >= %s AND s.ts <= %s
              AND s.amount_usd >= 10.0
        """
        params = [start_date, end_date]
        if token_where:
            query += f" AND ({token_where})"
            params.extend(token_params)
        if network_param:
            query += network_where
            params.append(network_param)
        query += "\nORDER BY s.ts"

        # Use a dedicated connection with a server-side named cursor
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        try:
            cur = conn.cursor(name='swaps_stream')
            cur.execute(query, params)
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                batch = []
                for row in rows:
                    tx_hash = row[0]
                    log_index = row[1]
                    batch.append({
                        'id': f"{tx_hash}#{log_index}",
                        'timestamp': int(row[2].timestamp()),
                        'tx_hash': tx_hash,
                        'token0_symbol': row[5],
                        'token1_symbol': row[6],
                        'amount0': float(row[7]) if row[7] is not None else 0.0,
                        'amount1': float(row[8]) if row[8] is not None else 0.0,
                        'amountUSD': float(row[9]) if row[9] is not None else 0.0,
                        'fee_tier': row[10] or '',
                        'protocol': row[4],
                        'network': row[3],
                        'log_index': log_index,
                    })
                yield batch
        finally:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.close()

    def fetch_pool_stats(self, pools: List[List[str]], start_date: datetime, end_date: datetime, prices: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """
        Fetch stats (APR) for a list of pools [(t0, t1, fee), ...] within date range.
        Returns dict: { "T0-T1-FEE": apr_float }

        Implementation note: this used to build one UNION ALL subquery *per pool*
        (plus a correlated TVL-fallback subquery per pool) and join
        liquidity_pool_history -> liquidity_pool on symbol pairs every time. That
        made latency grow linearly with the number of pools and defeated the
        symbol-pair indexes. The current shape resolves every requested pool to
        its pool_id(s) in ONE query, then runs ONE grouped aggregation over
        liquidity_pool_history keyed by pool_id, with ONE batched TVL-fallback
        query for pools that had no non-zero TVL in range. The volume fallback
        (swaps tables) is likewise collapsed to one grouped query per swap table.
        """
        if not pools:
            return {}

        try:
            conn = _get_pool().getconn()
            cur = conn.cursor()

            results = {}
            pool_meta = {}

            # Helper to normalize fee string to bips or whatever DB uses
            def normalize_fee(f):
                f_str = str(f).split('|')[0].replace('%', '').strip()
                if f_str == 'Dynamic':
                    return 'Dynamic'
                fee_map = {'0.01': '100', '0.05': '500', '0.08': '800', '0.3': '3000', '1.0': '10000'}
                if f_str in fee_map: return fee_map[f_str]

                try:
                    val = float(f_str)
                    if val > 100:
                        return 'Dynamic'
                    if val > 0 and val < 5:
                        return str(int(val * 10000))
                    return str(int(val))
                except:
                    return f_str

            # ------------------------------------------------------------------
            # Phase 0: normalize every requested pool into pool_meta[key].
            # ------------------------------------------------------------------
            all_networks = set()
            all_protocols = set()
            all_fee_variants = set()
            all_symbols = set()
            # (network, protocol, frozenset({t0,t1})) -> list of keys that could
            # match a pool row; used to fan out Phase 1 results without rescanning.
            pair_index = {}

            for p in pools:
                t0, t1, fee_raw_full = p
                t0_sym, t1_sym = t0.upper(), t1.upper()
                fee_raw = str(fee_raw_full).split('|')[0].strip()
                fee_db = normalize_fee(fee_raw_full)
                f_clean = fee_raw.replace('%', '').strip()

                network = "Ethereum"
                parts = str(fee_raw_full).split('|')
                if len(parts) >= 3:
                    network = parts[2].strip()

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

                fee_variants = [fee_db, f_clean, fee_raw]
                try:
                    if fee_db.isdigit():
                        val = float(fee_db) / 10000.0
                        fee_variants.append(f"{val:g}%")
                        fee_variants.append(f"{val:g}")
                except: pass
                fee_variants = list(set([v for v in fee_variants if v]))

                key = f"{t0}-{t1}-{fee_raw_full}"
                pool_meta[key] = {
                    't0_sym': t0_sym, 't1_sym': t1_sym, 'fee_db': fee_db, 'network': network,
                    'protocol': protocol, 'fee_variants': fee_variants, 'fee_raw_full': fee_raw_full,
                    'pool_ids': [], 'total_vol': 0.0, 'avg_tvl': 0.0,
                }

                all_networks.add(network)
                all_protocols.add(protocol)
                all_fee_variants.update(fee_variants)
                all_symbols.add(t0_sym)
                all_symbols.add(t1_sym)
                pair_index.setdefault((network, protocol, frozenset((t0_sym, t1_sym))), []).append(key)

            # ------------------------------------------------------------------
            # Phase 1: resolve pool_id(s) for ALL requested pools in ONE query.
            # We over-fetch by the union of symbols/fees/networks (liquidity_pool
            # is a small dimension table) and refine the symbol-pair + fee match
            # in Python via pair_index.
            # ------------------------------------------------------------------
            cur.execute("""
                SELECT lp.id, lp.network, lp.protocol, lp.fee_tier,
                       UPPER(c0.symbol), UPPER(c1.symbol)
                FROM liquidity_pool lp
                JOIN coin c0 ON lp.coin0_id = c0.coin_id
                JOIN coin c1 ON lp.coin1_id = c1.coin_id
                WHERE lp.network = ANY(%s)
                  AND lp.protocol = ANY(%s)
                  AND lp.fee_tier = ANY(%s)
                  AND UPPER(c0.symbol) = ANY(%s)
                  AND UPPER(c1.symbol) = ANY(%s)
            """, (
                list(all_networks), list(all_protocols), list(all_fee_variants),
                list(all_symbols), list(all_symbols),
            ))
            for pid, net, proto, fee_tier, c0, c1 in cur.fetchall():
                if c0 is None or c1 is None:
                    continue
                candidates = pair_index.get((net, proto, frozenset((c0, c1))))
                if not candidates:
                    continue
                for k in candidates:
                    meta = pool_meta[k]
                    # Symbol-pair order must match one direction; fee_tier must be
                    # in this pool's normalized variants.
                    if fee_tier not in meta['fee_variants']:
                        continue
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
            # Phase 2b: TVL fallback for keys with no non-zero TVL in range.
            # ONE batched DISTINCT ON query across every such pool_id, picking the
            # most recent non-zero TVL per pool_id; per key we take the latest by
            # date across its pool_ids (matches the old LIMIT-1 intent).
            # ------------------------------------------------------------------
            null_tvl_keys = [k for k, m in pool_meta.items() if m['avg_tvl'] <= 1.0 and m['pool_ids']]
            null_pool_ids = sorted({pid for k in null_tvl_keys for pid in pool_meta[k]['pool_ids']})
            if null_pool_ids:
                cur.execute("""
                    SELECT DISTINCT ON (pool_id) pool_id, ABS(tvl_usd) AS tvl, date
                    FROM liquidity_pool_history
                    WHERE pool_id = ANY(%s) AND tvl_usd <> 0
                    ORDER BY pool_id, date DESC
                """, (null_pool_ids,))
                # pool_id -> (date, tvl)
                fallback = {}
                for pid, tvl, dt in cur.fetchall():
                    fallback[pid] = (dt, float(tvl or 0))
                for k in null_tvl_keys:
                    best_date = None
                    best_tvl = 0.0
                    for pid in pool_meta[k]['pool_ids']:
                        fb = fallback.get(pid)
                        if not fb:
                            continue
                        fb_date, fb_tvl = fb
                        if fb_tvl <= 0:
                            continue
                        if best_date is None or (fb_date is not None and (best_date is None or fb_date > best_date)):
                            best_date = fb_date
                            best_tvl = fb_tvl
                    if best_tvl > 0:
                        pool_meta[k]['avg_tvl'] = best_tvl

            # ------------------------------------------------------------------
            # Phase 3: volume fallback from the swaps tables for keys still at
            # zero volume. Each key gets its own tightly-scoped subquery (one
            # exact symbol pair, one network/protocol, two fee-tier forms) so the
            # planner drives off the (network, timestamp) covering index and only
            # touches a small row set. Subqueries are UNION ALL'd in batches of 20
            # — one round-trip per batch. (A single grouped query with
            # token0=ANY(..) OR token1=ANY(..) over-fetches on the huge swaps
            # tables and was measured ~20x slower, so the per-pool scope stays.)
            # ------------------------------------------------------------------
            pool_queries_swaps = []
            params_swaps = []
            for k, meta in pool_meta.items():
                if meta.get('total_vol', 0) == 0:
                    fee_pct = str(meta['fee_raw_full']).split('|')[0]
                    fee_db = meta['fee_db']
                    if fee_db == 'Dynamic':
                        fee_tier_pct, fee_tier_bips = 'Dynamic', 'Dynamic'
                    else:
                        fee_tier_pct = fee_pct if '%' in fee_pct else {'100': '0.01%', '500': '0.05%', '800': '0.08%', '3000': '0.3%', '10000': '1.0%'}.get(fee_db, fee_pct)
                        fee_tier_bips = fee_db if fee_db.isdigit() else str(int(float(fee_pct.strip('%')) * 10000))

                    pool_queries_swaps.append("""
                    SELECT %s, c0.symbol, c1.symbol, SUM(s.amount_usd), SUM(ABS(s.amount0)), SUM(ABS(s.amount1))
                    FROM swaps s
                    JOIN coin c0 ON s.t0_coin_id = c0.coin_id
                    JOIN coin c1 ON s.t1_coin_id = c1.coin_id
                    WHERE s.ts >= %s AND s.ts <= %s AND s.network = %s AND s.protocol = %s
                    AND ((UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s) OR (UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s))
                    AND (s.fee_display = %s OR s.fee_display = %s)
                    GROUP BY c0.symbol, c1.symbol
                    """)
                    params_swaps.extend([k, start_date, end_date, meta['network'], meta['protocol'], meta['t0_sym'], meta['t1_sym'], meta['t1_sym'], meta['t0_sym'], fee_tier_pct, fee_tier_bips])

            if pool_queries_swaps:
                batch_size = 20
                for i in range(0, len(pool_queries_swaps), batch_size):
                    batch_queries = pool_queries_swaps[i:i+batch_size]
                    batch_params = params_swaps[i*11:(i+batch_size)*11]
                    cur.execute(" UNION ALL ".join(batch_queries), tuple(batch_params))
                    for row in cur.fetchall():
                        k = row[0]
                        usd_sum = float(row[3] or 0)
                        if usd_sum > 0:
                            pool_meta[k]['total_vol'] = pool_meta[k].get('total_vol', 0) + usd_sum
                        elif prices is not None:
                            p0 = prices.get(row[1]) or (1.0 if any(x in row[1].upper() for x in ['USD','EUR']) else 0)
                            p1 = prices.get(row[2]) or (1.0 if any(x in row[2].upper() for x in ['USD','EUR']) else 0)
                            pool_meta[k]['total_vol'] = pool_meta[k].get('total_vol', 0) + (float(row[4] or 0)*p0 + float(row[5] or 0)*p1)/2.0

            # Calculate APR
            for k, meta in pool_meta.items():
                avg_tvl = meta.get('avg_tvl', 0)
                total_vol = meta.get('total_vol', 0)
                t0_sym, t1_sym = meta['t0_sym'], meta['t1_sym']

                days = max(1, (end_date - start_date).days)
                is_unreliable_tvl = avg_tvl <= 1.0

                # Calculate fee rate
                fee_rate = None
                try:
                    fee_db = meta['fee_db']
                    if fee_db == 'Dynamic': fee_rate = 0.0002
                    elif '%' in fee_db: fee_rate = float(fee_db.replace('%', '').strip()) / 100.0
                    else: fee_rate = float(fee_db) / 1000000.0
                except:
                    pass

                apr = None
                if fee_rate == 0.0:
                    apr = 0.0
                elif fee_rate is not None and not is_unreliable_tvl:
                    try:
                        fees_earned = total_vol * fee_rate
                        apr = (fees_earned / avg_tvl) * (365.0 / days)
                    except:
                        pass

                if apr is not None:
                    results[k] = apr
                    # Reverse-token-order key (preserves the old behavior without
                    # the k.split('-') bug that broke on fees containing '-').
                    t0, t1, f = k.split('-', 2)
                    results[f"{t1}-{t0}-{f}"] = apr

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
