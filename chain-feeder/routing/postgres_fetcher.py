"""
Postgres Swap Data Fetcher

This module fetches swap data from the local Postgres database
for specified tokens within a given time range.
"""

import psycopg2
from datetime import datetime
from typing import List, Dict, Optional
from config import (
    DATA_WAREHOUSE_DB,
    ADDRESS_TO_SYMBOL
)

class PostgresFetcher:
    """Fetches swap data from local Postgres database"""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
    
    def _log(self, message: str):
        """Print log message if verbose mode is enabled"""
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [DB] {message}")
    
    def fetch_swaps(self, start_date: datetime, end_date: datetime, token_filter: Optional[List[str]] = None, network: Optional[str] = None) -> List[Dict]:
        """
        Fetch all swap events for tracked tokens within the date range from Postgres
        """
        self._log(f"Fetching swaps from {start_date} to {end_date} (network={network})")
        
        try:
            conn = psycopg2.connect(DATA_WAREHOUSE_DB)
            cur = conn.cursor()
            
            filter_sql = ""
            params = []
            upper_symbols = [symbol.upper() for symbol in token_filter] if token_filter else None
            
            if token_filter:
                if len(token_filter) > 10:
                    filter_sql += " AND (token0_symbol = ANY(%s) AND token1_symbol = ANY(%s))"
                else:
                    filter_sql += " AND (token0_symbol = ANY(%s) OR token1_symbol = ANY(%s))"
            if network and network.lower() != 'all':
                filter_sql += " AND network = %s"
            
            # Branch 1 (V3) parameters
            params.extend([start_date, end_date])
            if token_filter:
                params.extend([upper_symbols, upper_symbols])
            if network and network.lower() != 'all':
                params.append(network)
                
            # Branch 2 (V4) parameters
            params.extend([start_date, end_date])
            if token_filter:
                params.extend([upper_symbols, upper_symbols])
            if network and network.lower() != 'all':
                params.append(network)
                
            query = f"""
            SELECT 
                id, 
                timestamp, 
                tx_hash, 
                token0_address, 
                token1_address, 
                token0_symbol, 
                token1_symbol, 
                amount0, 
                amount1, 
                amount_usd, 
                fee_tier,
                protocol,
                network
            FROM uniswap_v3_swaps
            WHERE timestamp >= %s AND timestamp <= %s AND amount_usd >= 10.0 {filter_sql}
            UNION ALL
            SELECT 
                id, 
                timestamp, 
                tx_hash, 
                token0_address, 
                token1_address, 
                token0_symbol, 
                token1_symbol, 
                amount0, 
                amount1, 
                amount_usd, 
                fee_tier,
                protocol,
                network
            FROM uniswap_v4_swaps
            WHERE timestamp >= %s AND timestamp <= %s AND amount_usd >= 10.0 {filter_sql}
            """
            
            cur.execute(query, params)
            rows = cur.fetchall()
            
            swaps = []
            for row in rows:
                swaps.append({
                    'id': row[0],
                    'timestamp': int(row[1].timestamp()),
                    'tx_hash': row[2],
                    'token0_address': row[3],
                    'token1_address': row[4],
                    'token0_symbol': row[5],
                    'token1_symbol': row[6],
                    'amount0': float(row[7]),
                    'amount1': float(row[8]),
                    'amountUSD': float(row[9]),
                    'fee_tier': row[10],
                    'protocol': row[11],
                    'network': row[12]
                })
            
            cur.close()
            conn.close()
            
            self._log(f"Fetch complete. Total swaps from DB: {len(swaps)}")
            return swaps
            
        except Exception as e:
            self._log(f"Database query failed: {e}")
            raise

    def fetch_pool_stats(self, pools: List[List[str]], start_date: datetime, end_date: datetime, prices: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """
        Fetch stats (APR) for a list of pools [(t0, t1, fee), ...] within date range.
        Returns dict: { "T0-T1-FEE": apr_float }
        """
        if not pools:
            return {}
            
        try:
            conn = psycopg2.connect(DATA_WAREHOUSE_DB)
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

            pool_queries_history = []
            params_history = []
            
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
                    'protocol': protocol, 'fee_variants': fee_variants, 'fee_raw_full': fee_raw_full
                }
                
                pool_queries_history.append("""
                SELECT %s, SUM(ABS(h.volume_usd)), AVG(ABS(h.tvl_usd))
                FROM liquidity_pool_history h
                JOIN liquidity_pool p ON h.pool_id = p.id
                WHERE h.date >= %s::date AND h.date <= %s::date
                AND p.fee_tier = ANY(%s) AND p.network = %s AND p.protocol = %s
                AND ((UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s) OR (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s))
                """)
                params_history.extend([key, start_date, end_date, fee_variants, network, protocol, t0_sym, t1_sym, t1_sym, t0_sym])
                
            # Execute history
            if pool_queries_history:
                cur.execute(" UNION ALL ".join(pool_queries_history), tuple(params_history))
                for row in cur.fetchall():
                    k = row[0]
                    pool_meta[k]['total_vol'] = float(row[1] or 0)
                    pool_meta[k]['avg_tvl'] = float(row[2] or 0)
            
            # 2. TVL Fallback
            pool_queries_tvl = []
            params_tvl = []
            for k, meta in pool_meta.items():
                if meta.get('avg_tvl', 0) == 0:
                    pool_queries_tvl.append("""
                    SELECT %s, h.tvl_usd
                    FROM liquidity_pool_history h
                    JOIN liquidity_pool p ON h.pool_id = p.id
                    WHERE p.fee_tier = ANY(%s) AND p.network = %s AND p.protocol = %s AND h.tvl_usd != 0
                    AND ((UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s) OR (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s))
                    ORDER BY h.date DESC LIMIT 1
                    """)
                    params_tvl.extend([k, meta['fee_variants'], meta['network'], meta['protocol'], meta['t0_sym'], meta['t1_sym'], meta['t1_sym'], meta['t0_sym']])
            if pool_queries_tvl:
                # Need to wrap queries in parens for UNION ALL when using ORDER BY and LIMIT
                wrapped_queries = ["(" + q + ")" for q in pool_queries_tvl]
                cur.execute(" UNION ALL ".join(wrapped_queries), tuple(params_tvl))
                for row in cur.fetchall():
                    k = row[0]
                    pool_meta[k]['avg_tvl'] = abs(float(row[1] or 0))
                    
            # 3. Volume Fallback
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
                    
                    target_table = "uniswap_v4_swaps" if '|v4' in str(meta['fee_raw_full']).lower() else "uniswap_v3_swaps"
                    pool_queries_swaps.append(f"""
                    SELECT %s, token0_symbol, token1_symbol, SUM(amount_usd), SUM(ABS(amount0)), SUM(ABS(amount1))
                    FROM {target_table}
                    WHERE timestamp >= %s AND timestamp <= %s AND network = %s AND protocol = %s
                    AND ((token0_symbol = %s AND token1_symbol = %s) OR (token0_symbol = %s AND token1_symbol = %s))
                    AND (fee_tier = %s OR fee_tier = %s)
                    GROUP BY token0_symbol, token1_symbol
                    """)
                    params_swaps.extend([k, start_date, end_date, meta['network'], meta['protocol'], meta['t0_sym'], meta['t1_sym'], meta['t1_sym'], meta['t0_sym'], fee_tier_pct, fee_tier_bips])
                    
            if pool_queries_swaps:
                cur.execute(" UNION ALL ".join(pool_queries_swaps), tuple(params_swaps))
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
                
                if avg_tvl <= 1.0 and total_vol > 0.0:
                    stable_symbols = {'USD', 'USDT', 'USDC', 'DAI', 'EUR', 'EURC', 'BUSD', 'PYUSD', 'USDS'}
                    if t0_sym in stable_symbols and t1_sym in stable_symbols: avg_tvl = max(total_vol * 0.5, 1000000.0)
                    else: avg_tvl = max(total_vol * 1.2, 200000.0)
                    
                apr = None
                if avg_tvl > 1.0:
                    try:
                        fee_db = meta['fee_db']
                        if fee_db == 'Dynamic': fee_rate = 0.0002
                        elif '%' in fee_db: fee_rate = float(fee_db.replace('%', '').strip()) / 100.0
                        else: fee_rate = float(fee_db) / 1000000.0
                        
                        fees_earned = total_vol * fee_rate
                        days = max(1, (end_date - start_date).days)
                        apr = (fees_earned / avg_tvl) * (365.0 / days)
                    except: pass
                    
                if apr is not None:
                    results[k] = apr
                    t0, t1, f = k.split('-')
                    results[f"{t1}-{t0}-{f}"] = apr
                    
            cur.close()
            conn.close()
            return results
            
        except Exception as e:
            self._log(f"APR fetch failed: {e}")
            return {}

    def fetch_latest_prices(self) -> Dict[str, float]:
        """
        Fetch the most recent price for all tokens from coin_price_history.
        Returns dict: { "SYMBOL": price_float }
        """
        try:
            conn = psycopg2.connect(DATA_WAREHOUSE_DB)
            cur = conn.cursor()
            
            # Fetch the latest price for each symbol by joining with coin
            query = """
                SELECT DISTINCT ON (c.symbol) c.symbol, h.price
                FROM coin_price_history h
                JOIN coin c ON h.address = c.ethereum_address
                ORDER BY c.symbol, h.timestamp DESC
            """
            cur.execute(query)
            rows = cur.fetchall()
            
            prices = {row[0].upper(): float(row[1]) for row in rows if row[1] is not None}
            
            cur.close()
            conn.close()
            return prices
        except Exception as e:
            self._log(f"Latest price fetch failed: {e}")
            return {}
