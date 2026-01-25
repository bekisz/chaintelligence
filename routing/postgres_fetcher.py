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
