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
            ORDER BY timestamp ASC
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
            
            # Helper to normalize fee string to bips or whatever DB uses
            def normalize_fee(f):
                f_str = str(f).split('|')[0].replace('%', '').strip()
                if f_str == 'Dynamic':
                    return 'Dynamic'
                # Standard Uniswap V3 mappings
                fee_map = {'0.01': '100', '0.05': '500', '0.08': '800', '0.3': '3000', '1.0': '10000'}
                if f_str in fee_map: return fee_map[f_str]
                
                try:
                    val = float(f_str)
                    # Detect raw 0x800000 dynamic fee flag (838.8608% = 8388608/10000)
                    # and any absurdly high fee (>100%) — these are dynamic-fee pools
                    if val > 100:
                        return 'Dynamic'
                    # If it's a small number, it's likely a percentage (e.g. 0.0075 or 0.3)
                    # We want to return it as "points per million" (traditional bips * 100)
                    if val > 0 and val < 5: 
                        return str(int(val * 10000))
                    # Otherwise it's already in bips/ppm
                    return str(int(val))
                except:
                    return f_str

            for p in pools:
                t0, t1, fee = p
                t0_sym, t1_sym = t0.upper(), t1.upper()
                
                # Normalize fee to multiple possible formats found in DB
                fee_raw = str(fee).split('|')[0].strip()
                f_clean = fee_raw.replace('%', '').strip()
                fee_db = normalize_fee(fee)
                
                # Variants to try in DB: bips (e.g. '500'), percentage (e.g. '0.05%'), raw (e.g. '0.05')
                fee_variants = [fee_db, f_clean, fee_raw]
                try:
                    # add percentage if not present (f_clean might be '500' or '0.05')
                    if fee_db.isdigit():
                        val = float(fee_db) / 10000.0
                        fee_variants.append(f"{val:g}%")
                        fee_variants.append(f"{val:g}") # e.g. '0.05'
                except: pass
                # Remove duplicates and empty
                fee_variants = list(set([v for v in fee_variants if v]))
                
                network = "Ethereum"
                parts = str(fee).split('|')
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
                        protocol = proto_raw # e.g. PancakeSwap V3
                
                protocol_filter = f" AND p.protocol = '{protocol}'"
                
                # 1. Fetch aggregated stats for the window
                # Use ABS to handle potentially negative TVL/Volume from sync issues
                query = f"""
                SELECT 
                    SUM(ABS(h.volume_usd)),
                    AVG(ABS(h.tvl_usd))
                FROM liquidity_pool_history h
                JOIN liquidity_pool p ON h.pool_id = p.id
                WHERE 
                    h.date >= %s::date AND h.date <= %s::date
                    AND p.fee_tier = ANY(%s)
                    AND p.network = %s
                    {protocol_filter}
                    AND (
                        (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s)
                        OR 
                        (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s)
                    )
                """
                
                cur.execute(query, (start_date, end_date, fee_variants, network, t0_sym, t1_sym, t1_sym, t0_sym))
                row = cur.fetchone()
                
                total_vol = float(row[0]) if row and row[0] else 0
                avg_tvl = float(row[1]) if row and row[1] else 0

                # 2. TVL Fallback: If no TVL in window, try to get the latest known TVL for this pool
                if avg_tvl == 0:
                    cur.execute(f"""
                        SELECT h.tvl_usd, h.date
                        FROM liquidity_pool_history h
                        JOIN liquidity_pool p ON h.pool_id = p.id
                        WHERE p.fee_tier = ANY(%s)
                        AND p.network = %s
                        {protocol_filter}
                        AND h.tvl_usd != 0
                        AND (
                            (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s)
                            OR 
                            (UPPER(p.coin0_symbol) = %s AND UPPER(p.coin1_symbol) = %s)
                        )
                        ORDER BY h.date DESC LIMIT 1
                    """, (fee_variants, network, t0_sym, t1_sym, t1_sym, t0_sym))
                    tvl_row = cur.fetchone()
                    if tvl_row and tvl_row[0] is not None:
                        avg_tvl = abs(float(tvl_row[0]))

                # 3. Volume Fallback: If no volume in history table, check raw swaps
                if total_vol == 0 and avg_tvl > 0:
                    try:
                        # Ensure fee format matches swaps table (only the percentage part)
                        fee_pct = str(fee).split('|')[0]
                        if fee_db == 'Dynamic':
                            # Dynamic-fee pools: match the literal 'Dynamic' string in swaps table
                            fee_tier_pct = 'Dynamic'
                            fee_tier_bips = 'Dynamic'
                        else:
                            fee_map_rev = {'100': '0.01%', '500': '0.05%', '800': '0.08%', '3000': '0.3%', '10000': '1.0%'}
                            fee_tier_pct = fee_pct if '%' in fee_pct else fee_map_rev.get(fee_db, fee_pct)
                            fee_tier_bips = fee_db if fee_db.isdigit() else str(int(float(fee_pct.strip('%')) * 10000))
                        
                        target_table = "uniswap_v4_swaps" if '|v4' in str(fee).lower() else "uniswap_v3_swaps"
                        swap_query = f"""
                            SELECT token0_symbol, token1_symbol, SUM(amount_usd), SUM(ABS(amount0)), SUM(ABS(amount1))
                            FROM {target_table}
                            WHERE timestamp >= %s AND timestamp <= %s
                            AND network = %s
                            AND protocol = %s
                            AND (
                                (token0_symbol = %s AND token1_symbol = %s)
                                OR 
                                (token0_symbol = %s AND token1_symbol = %s)
                            )
                        """
                        params = [start_date, end_date, network, protocol, t0_sym.upper(), t1_sym.upper(), t1_sym.upper(), t0_sym.upper()]
                        
                        # Handle both formats in DB ('500' and '0.05%')
                        swap_query += " AND (fee_tier = %s OR fee_tier = %s) GROUP BY token0_symbol, token1_symbol"
                        params.extend([fee_tier_pct, fee_tier_bips])
                        cur.execute(swap_query, tuple(params))

                        total_fallback_vol = 0
                        for sw_row in cur.fetchall():
                            t0_row_sym = sw_row[0]
                            t1_row_sym = sw_row[1]
                            usd_sum = float(sw_row[2]) if sw_row[2] else 0
                            amt0_sum = float(sw_row[3]) if sw_row[3] else 0
                            amt1_sum = float(sw_row[4]) if sw_row[4] else 0
                            if usd_sum > 0:
                                total_fallback_vol += usd_sum
                            elif prices is not None:
                                p0 = prices.get(t0_row_sym)
                                p1 = prices.get(t1_row_sym)
                                if not p0:
                                     sym = t0_row_sym.upper()
                                     if any(x in sym for x in ['USD', 'EUR', 'DAI', 'USDC', 'USDT', 'EURC']): p0 = 1.0
                                if not p1:
                                     sym = t1_row_sym.upper()
                                     if any(x in sym for x in ['USD', 'EUR', 'DAI', 'USDC', 'USDT', 'EURC']): p1 = 1.0
                                p0 = p0 or 0
                                p1 = p1 or 0
                                total_fallback_vol += (amt0_sum * p0 + amt1_sum * p1) / 2.0
                                
                        total_vol = total_fallback_vol
                    except Exception as e:
                        self._log(f"Fallback volume calc failed: {e}")

                # 4. Final APR Calculation
                if avg_tvl <= 1.0 and total_vol > 0.0:
                    stable_symbols = {'USD', 'USDT', 'USDC', 'DAI', 'EUR', 'EURC', 'BUSD', 'PYUSD', 'USDS', 'USD1'}
                    if t0_sym.upper() in stable_symbols and t1_sym.upper() in stable_symbols:
                        avg_tvl = max(total_vol * 0.5, 1000000.0)
                    else:
                        avg_tvl = max(total_vol * 1.2, 200000.0)

                apr = None
                if avg_tvl > 1.0: # Minimum TVL to calculate APR
                    try:
                        if fee_db == 'Dynamic':
                            # Dynamic-fee pools (Uniswap V4 hooks): use conservative
                            # effective rate. Typical range for major pairs: 0.015%-0.025%.
                            fee_rate = 0.0002  # 2 bps (0.02%)
                        else:
                            if '%' in fee_db:
                                fee_rate = float(fee_db.replace('%', '').strip()) / 100.0
                            else:
                                fee_rate = float(fee_db) / 1000000.0
                        fees_earned = total_vol * fee_rate
                        
                        days = (end_date - start_date).days
                        if days < 1: days = 1
                        
                        apr = (fees_earned / avg_tvl) * (365.0 / days)
                    except:
                        pass
                
                if apr is not None:
                    key = f"{t0}-{t1}-{fee}"
                    results[key] = apr
                    key_rev = f"{t1}-{t0}-{fee}"
                    results[key_rev] = apr
            
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
