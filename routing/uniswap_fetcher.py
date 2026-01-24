"""
Uniswap V3 Swap Data Fetcher

This module fetches swap data from The Graph's Uniswap V3 subgraph
for specified tokens within a given time range.
"""

import requests
import time
from datetime import datetime
from typing import List, Dict, Optional
from config import (
    UNISWAP_V3_SUBGRAPH_URL,
    TOKEN_ADDRESSES,
    ADDRESS_TO_SYMBOL,
    MAX_RESULTS_PER_QUERY,
    REQUEST_TIMEOUT,
    MAX_RETRIES
)


class UniswapV3Fetcher:
    """Fetches swap data from Uniswap V3 subgraph"""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.session = requests.Session()
    
    def _log(self, message: str):
        """Print log message if verbose mode is enabled"""
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
    
    def _build_swap_query(self, start_timestamp: int, end_timestamp: int, filter_field: str, filter_values: List[str]) -> str:
        """Build GraphQL query for swap events with specific filter"""
        # Format addresses for GraphQL array
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
            transaction {{
              id
            }}
            token0 {{
              id
              symbol
            }}
            token1 {{
              id
              symbol
            }}
            amount0
            amount1
            amountUSD
          }}
        }}
        """
        return query
    
    def _execute_query(self, query: str) -> Optional[Dict]:
        """Execute GraphQL query with retry logic"""
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
            
            except requests.exceptions.RequestException as e:
                self._log(f"Request failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    raise
        
        return None
    
    def _fetch_swaps_with_filter(self, start_timestamp: int, end_timestamp: int, filter_field: str, filter_addresses: List[str]) -> List[Dict]:
        """Fetch all swaps matching a specific filter condition using cursor pagination"""
        self._log(f"Fetching swaps where {filter_field} matches target tokens...")
        
        found_swaps = []
        current_start_time = start_timestamp
        
        while True:
            query = self._build_swap_query(current_start_time, end_timestamp, filter_field, filter_addresses)
            result = self._execute_query(query)
            
            if not result or 'data' not in result:
                self._log(f"No data returned for {filter_field} query")
                break
            
            swaps = result['data'].get('swaps', [])
            
            if not swaps:
                break
            
            # Normalize swaps immediately
            swaps_added = 0
            last_timestamp = 0
            
            # Helper for safe conversion
            to_float = lambda x: float(x) if x is not None else 0.0
            to_int = lambda x: int(x) if x is not None else 0
            
            for swap in swaps:
                # Safe access to nested objects
                token0 = swap.get('token0') or {}
                token1 = swap.get('token1') or {}
                transaction = swap.get('transaction') or {}
                
                t0_addr = token0.get('id', '').lower()
                t1_addr = token1.get('id', '').lower()
                
                normalized_swap = {
                    'id': swap.get('id', 'unknown'),
                    'timestamp': to_int(swap.get('timestamp')),
                    'tx_hash': transaction.get('id', 'unknown'),
                    'token0_address': t0_addr,
                    'token1_address': t1_addr,
                    'token0_symbol': ADDRESS_TO_SYMBOL.get(t0_addr, token0.get('symbol') or 'UNKNOWN'),
                    'token1_symbol': ADDRESS_TO_SYMBOL.get(t1_addr, token1.get('symbol') or 'UNKNOWN'),
                    'amount0': to_float(swap.get('amount0')),
                    'amount1': to_float(swap.get('amount1')),
                    'amountUSD': to_float(swap.get('amountUSD'))
                }
                found_swaps.append(normalized_swap)
                last_timestamp = normalized_swap['timestamp']
                swaps_added += 1
            
            last_date = datetime.fromtimestamp(last_timestamp).strftime('%Y-%m-%d %H:%M:%S')
            self._log(f"Fetched {len(swaps)} swaps for {filter_field} (Total: {len(found_swaps)}) - Last TS: {last_timestamp} ({last_date})")
            
            if len(swaps) < MAX_RESULTS_PER_QUERY:
                break
                
            # Update cursor for next page
            # We use the timestamp of the last item.
            # If the last item has same timestamp as current_start_time, we might get stuck in a loop
            # if there are > 1000 items in the same second. 
            # But standard behavior is strict ordering by timestamp. 
            # If we set new start to last_timestamp, we rely on final deduplication to remove overlaps.
            
            if last_timestamp == current_start_time:
                # Edge case: Entire page has same timestamp. 
                # We simply increment to next second to avoid infinite loop.
                # This risks missing some swaps in that exact second but prevents hanging.
                current_start_time += 1
            else:
                current_start_time = last_timestamp
                
        return found_swaps

    def fetch_swaps(self, start_date: datetime, end_date: datetime, token_filter: Optional[List[str]] = None) -> List[Dict]:
        """
        Fetch all swap events for tracked tokens within the date range using optimized queries
        """
        start_timestamp = int(start_date.timestamp())
        end_timestamp = int(end_date.timestamp())
        
        # Determine which token addresses to use
        if token_filter:
            from config import TOKENS
            filtered_addresses = [TOKENS[symbol]['address'].lower() for symbol in token_filter]
            self._log(f"Filtering to {len(token_filter)} tokens: {', '.join(token_filter)}")
        else:
            filtered_addresses = TOKEN_ADDRESSES
        
        self._log(f"Fetching swaps from {start_date} to {end_date}")
        self._log(f"Timestamp range: {start_timestamp} to {end_timestamp}")
        
        # Method 1: Fetch where token0 is in our list
        swaps_token0 = self._fetch_swaps_with_filter(
            start_timestamp, end_timestamp, "token0_in", filtered_addresses
        )
        
        # Method 2: Fetch where token1 is in our list
        swaps_token1 = self._fetch_swaps_with_filter(
            start_timestamp, end_timestamp, "token1_in", filtered_addresses
        )
        
        # Merge and deduplicate by ID
        unique_swaps = {}
        
        for swap in swaps_token0:
            unique_swaps[swap['id']] = swap
            
        for swap in swaps_token1:
            unique_swaps[swap['id']] = swap
            
        final_swaps = list(unique_swaps.values())
        
        # Sort by timestamp to maintain order
        final_swaps.sort(key=lambda x: x['timestamp'])
        
        self._log(f"Fetch complete. Total unique swaps: {len(final_swaps)}")
        return final_swaps
