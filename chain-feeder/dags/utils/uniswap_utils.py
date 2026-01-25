import requests
import time
import psycopg2
from datetime import datetime
from typing import List, Dict, Optional
from utils.config import (
    UNISWAP_V3_SUBGRAPH_URL,
    TOKEN_ADDRESSES,
    ADDRESS_TO_SYMBOL,
    MAX_RESULTS_PER_QUERY,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    DATA_WAREHOUSE_DB
)

class UniswapV3Fetcher:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.session = requests.Session()
    
    def _log(self, message: str):
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
    
    def _build_swap_query(self, start_timestamp: int, end_timestamp: int, filter_field: str, filter_values: List[str]) -> str:
        addr_list = str(filter_values).replace("'", '"')
        query = f"""
        {{
          swaps(
            first: {MAX_RESULTS_PER_QUERY}
            orderBy: timestamp
            orderDirection: asc
            where: {{
              timestamp_gte: {start_timestamp}
              timestamp_lte: {end_timestamp}
              {filter_field}: {addr_list}
            }}
          ) {{
            id
            timestamp
            transaction {{ id }}
            token0 {{ id symbol }}
            token1 {{ id symbol }}
            amount0
            amount1
            amountUSD
            pool {{ feeTier }}
          }}
        }}
        """
        return query
    
    def _execute_query(self, query: str) -> Optional[Dict]:
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.post(
                    UNISWAP_V3_SUBGRAPH_URL,
                    json={'query': query},
                    timeout=REQUEST_TIMEOUT
                )
                response.raise_for_status()
                data = response.json()
                if 'errors' in data:
                    self._log(f"GraphQL errors: {data['errors']}")
                    return None
                return data
            except Exception as e:
                self._log(f"Request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
        return None
    
    def _fetch_swaps_with_filter(self, start_timestamp: int, end_timestamp: int, filter_field: str, filter_addresses: List[str], on_batch_callback: Optional[callable] = None) -> List[Dict]:
        found_swaps = []
        current_start_time = start_timestamp
        while True:
            query = self._build_swap_query(current_start_time, end_timestamp, filter_field, filter_addresses)
            result = self._execute_query(query)
            if not result or 'data' not in result:
                break
            swaps = result['data'].get('swaps', [])
            if not swaps:
                break
            
            to_float = lambda x: float(x) if x is not None else 0.0
            to_int = lambda x: int(x) if x is not None else 0
            
            batch_swaps = []
            last_timestamp = 0
            for swap in swaps:
                token0 = swap.get('token0') or {}
                token1 = swap.get('token1') or {}
                transaction = swap.get('transaction') or {}
                t0_addr = token0.get('id', '').lower()
                t1_addr = token1.get('id', '').lower()
                
                normalized = {
                    'id': swap.get('id', 'unknown'),
                    'timestamp': to_int(swap.get('timestamp')),
                    'tx_hash': transaction.get('id', 'unknown'),
                    'token0_address': t0_addr,
                    'token1_address': t1_addr,
                    'token0_symbol': ADDRESS_TO_SYMBOL.get(t0_addr, token0.get('symbol') or 'UNKNOWN'),
                    'token1_symbol': ADDRESS_TO_SYMBOL.get(t1_addr, token1.get('symbol') or 'UNKNOWN'),
                    'amount0': to_float(swap.get('amount0')),
                    'amount1': to_float(swap.get('amount1')),
                    'amountUSD': to_float(swap.get('amountUSD')),
                    'fee_tier': f"{to_float((swap.get('pool') or {}).get('feeTier')) / 10000}%"
                }
                batch_swaps.append(normalized)
                last_timestamp = normalized['timestamp']
            
            found_swaps.extend(batch_swaps)
            self._log(f"Fetched {len(batch_swaps)} swaps from {filter_field} (Total: {len(found_swaps)})")
            
            if on_batch_callback:
                on_batch_callback(batch_swaps)
            
            if len(swaps) < MAX_RESULTS_PER_QUERY:
                break
            current_start_time = last_timestamp + 1 if last_timestamp == current_start_time else last_timestamp
        return found_swaps

    def fetch_swaps(self, start_date: datetime, end_date: datetime, on_batch_callback: Optional[callable] = None) -> List[Dict]:
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())
        swaps_token0 = self._fetch_swaps_with_filter(start_ts, end_ts, "token0_in", TOKEN_ADDRESSES, on_batch_callback)
        swaps_token1 = self._fetch_swaps_with_filter(start_ts, end_ts, "token1_in", TOKEN_ADDRESSES, on_batch_callback)
        unique = {s['id']: s for s in swaps_token0 + swaps_token1}
        return sorted(unique.values(), key=lambda x: x['timestamp'])

class PostgresStorage:
    def __init__(self):
        self.conn_str = DATA_WAREHOUSE_DB
    
    def save_swaps(self, swaps: List[Dict]):
        if not swaps:
            return
        
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                insert_query = """
                INSERT INTO uniswap_v3_swaps (
                    id, timestamp, tx_hash, token0_address, token1_address, 
                    token0_symbol, token1_symbol, amount0, amount1, amount_usd, fee_tier
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING;
                """
                data = [
                    (
                        s['id'],
                        datetime.fromtimestamp(s['timestamp']),
                        s['tx_hash'],
                        s['token0_address'],
                        s['token1_address'],
                        s['token0_symbol'],
                        s['token1_symbol'],
                        s['amount0'],
                        s['amount1'],
                        s['amountUSD'],
                        s['fee_tier']
                    ) for s in swaps
                ]
                cur.executemany(insert_query, data)
            conn.commit()

    def get_last_swap_timestamp(self) -> Optional[int]:
        """
        Get the timestamp of the latest swap stored in the database.
        Returns None if the table is empty.
        """
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(timestamp) FROM uniswap_v3_swaps")
                res = cur.fetchone()
                if res and res[0]:
                    return int(res[0].timestamp())
        return None
