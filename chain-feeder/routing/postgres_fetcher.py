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
    
    def fetch_swaps(self, start_date: datetime, end_date: datetime, token_filter: Optional[List[str]] = None) -> List[Dict]:
        """
        Fetch all swap events for tracked tokens within the date range from Postgres
        """
        self._log(f"Fetching swaps from {start_date} to {end_date}")
        
        try:
            conn = psycopg2.connect(DATA_WAREHOUSE_DB)
            cur = conn.cursor()
            
            query = """
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
                fee_tier
            FROM uniswap_v3_swaps
            WHERE timestamp >= %s AND timestamp <= %s
            """
            params = [start_date, end_date]
            
            if token_filter:
                from config import TOKENS
                filtered_addresses = [TOKENS[symbol]['address'].lower() for symbol in token_filter]
                query += " AND (token0_address = ANY(%s) OR token1_address = ANY(%s))"
                params.extend([filtered_addresses, filtered_addresses])
            
            query += " ORDER BY timestamp ASC"
            
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
                    'fee_tier': row[10]
                })
            
            cur.close()
            conn.close()
            
            self._log(f"Fetch complete. Total swaps from DB: {len(swaps)}")
            return swaps
            
        except Exception as e:
            self._log(f"Database query failed: {e}")
            raise

    def fetch_pool_stats(self, pools: List[List[str]], start_date: datetime, end_date: datetime) -> Dict[str, float]:
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
            
            # We must process one by one or build a complex WHERE clause. 
            # Given we have ~200 routes max, but many duplicates, unique pools likely < 50.
            # Let's iterate.
            
            # Helper to normalize fee string to bips or whatever DB uses
            # DB uses strings like '100', '500', '3000', '10000'
            # Input fee is like '0.05%'
            def normalize_fee(f):
                if f == '0.01%': return '100'
                if f == '0.05%': return '500'
                if f == '0.3%': return '3000'
                if f == '1.0%': return '10000'
                # fallback for raw values
                return f.replace('%', '')

            for p in pools:
                t0, t1, fee = p
                t0, t1 = t0.upper(), t1.upper()
                fee_db = normalize_fee(fee)
                
                # Fetch aggregated stats
                # We need to match (coin0, coin1) OR (coin1, coin0)
                query = """
                SELECT 
                    SUM(h.volume_usd),
                    AVG(h.tvl_usd)
                FROM liquidity_pool_history h
                JOIN liquidity_pool p ON h.pool_id = p.id
                WHERE 
                    h.date >= %s::date AND h.date <= %s::date
                    AND p.fee_tier = %s
                    AND (
                        (p.coin0_symbol = %s AND p.coin1_symbol = %s)
                        OR 
                        (p.coin0_symbol = %s AND p.coin1_symbol = %s)
                    )
                """
                
                cur.execute(query, (start_date, end_date, fee_db, t0, t1, t1, t0))
                row = cur.fetchone()
                
                total_vol = float(row[0]) if row and row[0] else 0
                avg_tvl = float(row[1]) if row and row[1] else 0

                # Fallback: If volume is 0, try raw swaps
                if total_vol == 0 and avg_tvl > 0:
                    try:
                        # 1. Get raw token volume
                        # Determine fee_tier string for swaps table (e.g. '0.05%')
                        # Input 'fee' might be '500' or '0.05%'
                        fee_tier_str = fee if '%' in fee else f"{float(fee)/10000:.2f}%".replace("0.", "0.0") # Approximating, safer to rely on passed fee if it had %
                        # Actually the input 'p' tuple usually comes from 'route.path_tokens' which has '0.05%' style strings.
                        # But just in case, let's try to match what's in DB.
                        
                        # Simplified: Just query by tokens and ignore fee if specific string match is hard, 
                        # OR better: assume input 'fee' is correct format if it has %
                        
                        swap_query = """
                            SELECT SUM(ABS(amount0))
                            FROM uniswap_v3_swaps
                            WHERE timestamp >= %s AND timestamp <= %s
                            AND (
                                (UPPER(token0_symbol) = %s AND UPPER(token1_symbol) = %s)
                                OR 
                                (UPPER(token0_symbol) = %s AND UPPER(token1_symbol) = %s)
                            )
                        """
                        # If we want to be strict on fee: "AND fee_tier = %s"
                        # But let's check if we can skip fee filter for safety or if we need it. 
                        # Multi-fee pools exist. We should try to use it.
                        if '%' in fee:
                             swap_query += " AND fee_tier = %s"
                             cur.execute(swap_query, (start_date, end_date, t0, t1, t1, t0, fee))
                        else:
                             # If fee is '500', difficult to map back exactly without knowing convention. 
                             # Let's Skip fee filter if we can't easily match, or assume standard mapping
                             cur.execute(swap_query, (start_date, end_date, t0, t1, t1, t0))

                        swap_row = cur.fetchone()
                        raw_vol_token0 = float(swap_row[0]) if swap_row and swap_row[0] else 0
                        
                        if raw_vol_token0 > 0:
                            # 2. Get Price (Case Insensitive)
                            cur.execute("SELECT price FROM coin_price_history WHERE UPPER(symbol) = %s ORDER BY timestamp DESC LIMIT 1", (t0.upper(),))
                            price_row = cur.fetchone()
                            price = float(price_row[0]) if price_row and price_row[0] else 0
                            
                            if price > 0:
                                total_vol = raw_vol_token0 * price
                                # self._log(f"Fallback calculated volume for {t0}-{t1}: ${total_vol:,.2f}")
                    except Exception as e:
                        self._log(f"Fallback volume calc failed: {e}")

                apr = None
                if avg_tvl > 0:
                    # Calculate Fee earned
                    try:
                        fee_rate = float(fee_db) / 1000000.0 
                    except:
                        fee_rate = 0
                        
                    fees_earned = total_vol * fee_rate
                    
                    # Annualize
                    days = (end_date - start_date).days
                    if days < 1: days = 1
                    
                    apr = (fees_earned / avg_tvl) * (365.0 / days)
                
                if apr is not None:
                    key = f"{t0}-{t1}-{fee}"
                    results[key] = apr
                    # Also store reverse key just in case
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
            
            # Fetch the latest price for each symbol
            query = """
                SELECT DISTINCT ON (symbol) symbol, price
                FROM coin_price_history
                ORDER BY symbol, timestamp DESC
            """
            cur.execute(query)
            rows = cur.fetchall()
            
            prices = {row[0].upper(): float(row[1]) for row in rows}
            
            cur.close()
            conn.close()
            return prices
        except Exception as e:
            self._log(f"Latest price fetch failed: {e}")
            return {}
