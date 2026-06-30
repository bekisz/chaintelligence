import requests
import time
import psycopg2
from datetime import datetime, timezone
from typing import List, Dict, Optional
from .config import (
    UNISWAP_V3_SUBGRAPH_URL,
    UNISWAP_V4_SUBGRAPH_URL,
    TOKEN_ADDRESSES,
    ADDRESS_TO_SYMBOL,
    MAX_RESULTS_PER_QUERY,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    DATA_WAREHOUSE_DB
)

# Arbitrum token addresses — verified against live V3 and V4 subgraph data
ARBITRUM_TOKEN_ADDRESSES = {
    # Native ETH (used as address 0x000… in V4)
    'ETH':  '0x0000000000000000000000000000000000000000',
    # Bridged USDC (used in V3)
    'USDC.e': '0xff970a61a04b1ca14834a43f5de4533ebddb5cc8',
    # Native USDC (used in V4)
    'USDC': '0xaf88d065e77c8cc2239327c5edb3a432268e5831',
    # Wrapped ETH token (used in V3)
    'WETH': '0x82af49447d8a07e3bd95bd0d56f35241523fbab1',
    'USDT': '0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9',
    'WBTC': '0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f',
    'DAI':  '0xda10009c55681e77d502082691d29f8fb095569f',
    'LINK': '0xf97f4df75117a78c1a5a0dbb814af92458539fb4',
    'GMX':  '0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a',
    'AAVE': '0xba5ddd1f9d7f570dc94a51479a000e3bce967196',
    'ZRO':  '0x6985884c4392d348587b19cb9eaaf157f13271cd',
}

class UniswapV3Fetcher:
    def __init__(self, verbose: bool = False, network: str = "Ethereum"):
        self.verbose = verbose
        self.network = network
        self.session = requests.Session()

        # Build network-aware V3 and V4 URLs
        import os
        GRAPH_API_KEY = os.getenv('GRAPH_API_KEY', '')
        if self.network == "Arbitrum":
            v3_subgraph_id = "FbCGRftH4a3yZugY7TnbYgPJVEv2LvMT6oF1fxPe9aJM"  # Uniswap V3 Arbitrum swaps (verified)
            v4_subgraph_id = "G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"  # Uniswap V4 Arbitrum swaps
        else: # Ethereum
            v3_subgraph_id = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
            v4_subgraph_id = "DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

        if not GRAPH_API_KEY or GRAPH_API_KEY == 'YOUR_GRAPH_API_KEY':
            self.subgraph_url = f'https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/{v3_subgraph_id}'
            self.subgraph_v4_url = f'https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/{v4_subgraph_id}'
        else:
            self.subgraph_url = f'https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{v3_subgraph_id}'
            self.subgraph_v4_url = f'https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{v4_subgraph_id}'
    
    def _log(self, message: str):
        if self.verbose:
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}")
    
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
                    self.subgraph_url,
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
    
    def _fetch_swaps_with_filter(self, start_timestamp: int, end_timestamp: int, filter_field: str, filter_addresses: List[str], on_batch_callback: Optional[callable] = None, collect_results: bool = True) -> List:
        found_results = []
        seen_ids = set()
        current_start_time = start_timestamp

        if self.network == "Arbitrum":
            addr_to_sym = {addr.lower(): sym for sym, addr in ARBITRUM_TOKEN_ADDRESSES.items()}
        else:
            addr_to_sym = ADDRESS_TO_SYMBOL

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
                swap_id = swap.get('id', 'unknown')
                if swap_id in seen_ids:
                    continue
                seen_ids.add(swap_id)

                token0 = swap.get('token0') or {}
                token1 = swap.get('token1') or {}
                transaction = swap.get('transaction') or {}
                t0_addr = token0.get('id', '').lower()
                t1_addr = token1.get('id', '').lower()

                fee_tier_val = to_int((swap.get('pool') or {}).get('feeTier'))
                if fee_tier_val & 0x800000:
                    fee_tier_str = "Dynamic"
                else:
                    fee_tier_str = f"{to_float(fee_tier_val) / 10000}%"

                normalized = {
                    'id': swap_id,
                    'timestamp': to_int(swap.get('timestamp')),
                    'tx_hash': transaction.get('id', 'unknown'),
                    'token0_address': t0_addr,
                    'token1_address': t1_addr,
                    'token0_symbol': addr_to_sym.get(t0_addr, token0.get('symbol') or 'UNKNOWN').upper(),
                    'token1_symbol': addr_to_sym.get(t1_addr, token1.get('symbol') or 'UNKNOWN').upper(),
                    'amount0': to_float(swap.get('amount0')),
                    'amount1': to_float(swap.get('amount1')),
                    'amountUSD': to_float(swap.get('amountUSD')),
                    'fee_tier': fee_tier_str
                }
                batch_swaps.append(normalized)
            
            # The last element's timestamp in the original batch
            last_timestamp = to_int(swaps[-1].get('timestamp'))

            if collect_results:
                found_results.extend(batch_swaps)
            else:
                found_results.extend([s['id'] for s in batch_swaps])

            self._log(f"Fetched {len(batch_swaps)} new swaps from {filter_field} (Total: {len(found_results)})")

            if on_batch_callback and batch_swaps:
                on_batch_callback(batch_swaps)

            if len(swaps) < MAX_RESULTS_PER_QUERY:
                break
            
            # Advance to the last timestamp. If we got stuck (e.g. >1000 swaps exactly on this second), push forward by 1s
            if last_timestamp == current_start_time:
                current_start_time += 1
            else:
                current_start_time = last_timestamp

        return found_results

    def fetch_swaps(self, start_date: datetime, end_date: datetime, on_batch_callback: Optional[callable] = None, collect_results: bool = True):
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())
        
        if self.network == "Arbitrum":
            addresses = [addr.lower() for addr in ARBITRUM_TOKEN_ADDRESSES.values()]
        else:
            addresses = TOKEN_ADDRESSES
            
        swaps_token0 = self._fetch_swaps_with_filter(start_ts, end_ts, "token0_in", addresses, on_batch_callback, collect_results=collect_results)
        swaps_token1 = self._fetch_swaps_with_filter(start_ts, end_ts, "token1_in", addresses, on_batch_callback, collect_results=collect_results)
        
        if collect_results:
            unique = {s['id']: s for s in swaps_token0 + swaps_token1}
            return sorted(unique.values(), key=lambda x: x['timestamp'])
        else:
            # Just count unique IDs to save memory
            unique_ids = set(swaps_token0 + swaps_token1)
            return len(unique_ids)

    def fetch_pool_daily_data(self, token0_addr: str, token1_addr: str, fee_tier_bips: int, start_date: datetime) -> List[Dict]:
        """
        Fetches daily pool data for a specific pool defined by token pair and fee tier.
        This queries the 'pools' entity first to find the ID, then 'poolDayDatas'.
        """
        start_ts = int(start_date.timestamp())
        
        # Ensure consistent token ordering for graph query
        t0, t1 = sorted([token0_addr.lower(), token1_addr.lower()])
        
        query = f"""
        {{
          pools(where: {{
            token0: "{t0}", 
            token1: "{t1}", 
            feeTier: {fee_tier_bips}
          }}) {{
            id
            poolDayData(
                where: {{ date_gte: {start_ts} }}
                orderBy: date
                orderDirection: asc
            ) {{
              date
              tvlUSD
              volumeUSD
              txCount
            }}
          }}
        }}
        """
        
        result = self._execute_query(query)
        if not result or 'data' not in result:
            return []
            
        pools = result['data'].get('pools', [])
        if not pools:
            return []
            
        # Should be only one pool, but take the first
        pool_data = pools[0]
        day_datas = pool_data.get('poolDayData', [])
        
        normalized_data = []
        for d in day_datas:
             normalized_data.append({
                 'date': datetime.fromtimestamp(int(d['date']), timezone.utc).date(),
                 'tvl_usd': float(d.get('tvlUSD', 0) or 0),
                 'volume_usd': float(d.get('volumeUSD', 0) or 0),
                 'tx_count': int(d.get('txCount', 0) or 0),
             })
             
        return normalized_data

class UniswapV4Fetcher(UniswapV3Fetcher):
    def _execute_query(self, query: str) -> Optional[Dict]:
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.post(
                    self.subgraph_v4_url,
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

    def fetch_pool_daily_data(self, token0_addr: str, token1_addr: str, fee_tier_bips: int, start_date: datetime) -> List[Dict]:
        """
        Fetches daily pool data for a specific pool defined by token pair and fee tier.
        For Uniswap V4, there can be multiple pools with the same fee tier (due to spacing/hooks),
        so we query totalValueLockedUSD and select the pool with the highest absolute TVL.
        """
        start_ts = int(start_date.timestamp())
        t0, t1 = sorted([token0_addr.lower(), token1_addr.lower()])
        
        query = f"""
        {{
          pools(where: {{
            token0: "{t0}", 
            token1: "{t1}", 
            feeTier: "{fee_tier_bips}"
          }}) {{
            id
            totalValueLockedUSD
            poolDayData(
                where: {{ date_gte: {start_ts} }}
                orderBy: date
                orderDirection: asc
            ) {{
              date
              tvlUSD
              volumeUSD
              txCount
            }}
          }}
        }}
        """
        
        result = self._execute_query(query)
        if not result or 'data' not in result:
            return []
            
        pools = result['data'].get('pools', [])
        if not pools:
            return []
            
        # Select pool with highest absolute totalValueLockedUSD
        valid_pools = []
        for p in pools:
            try:
                tvl = abs(float(p.get('totalValueLockedUSD') or 0))
            except ValueError:
                tvl = 0.0
            valid_pools.append((tvl, p))
            
        if not valid_pools:
            return []
            
        valid_pools.sort(key=lambda x: x[0], reverse=True)
        pool_data = valid_pools[0][1]
        
        day_datas = pool_data.get('poolDayData', [])
        
        normalized_data = []
        for d in day_datas:
             normalized_data.append({
                 'date': datetime.fromtimestamp(int(d['date']), timezone.utc).date(),
                 'tvl_usd': float(d.get('tvlUSD', 0) or 0),
                 'volume_usd': float(d.get('volumeUSD', 0) or 0),
                 'tx_count': int(d.get('txCount', 0) or 0),
             })
             
        return normalized_data

class PostgresStorage:
    def __init__(self):
        self.conn_str = DATA_WAREHOUSE_DB
    
    def save_swaps(self, swaps: List[Dict], network: str = "Ethereum"):
        if not swaps:
            return
        
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                insert_query = """
                INSERT INTO uniswap_v3_swaps (
                    id, timestamp, tx_hash, token0_address, token1_address, 
                    token0_symbol, token1_symbol, amount0, amount1, amount_usd, fee_tier, network
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING;
                """
                data = [
                    (
                        s['id'],
                        datetime.fromtimestamp(s['timestamp'], timezone.utc),
                        s['tx_hash'],
                        s['token0_address'],
                        s['token1_address'],
                        s['token0_symbol'],
                        s['token1_symbol'],
                        s['amount0'],
                        s['amount1'],
                        s['amountUSD'],
                        s['fee_tier'],
                        network
                    ) for s in swaps
                ]
                cur.executemany(insert_query, data)
            conn.commit()

    def get_last_swap_timestamp(self, network: str = "Ethereum") -> Optional[int]:
        """
        Get the timestamp of the latest swap stored in the database.
        Returns None if the table is empty.
        """
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(timestamp) FROM uniswap_v3_swaps WHERE network = %s", (network,))
                res = cur.fetchone()
                if res and res[0]:
                    return int(res[0].timestamp())
        return None

class PostgresStorageV4(PostgresStorage):
    def save_swaps(self, swaps: List[Dict], network: str = "Ethereum"):
        if not swaps:
            return
        
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                insert_query = """
                INSERT INTO uniswap_v4_swaps (
                    id, timestamp, tx_hash, token0_address, token1_address, 
                    token0_symbol, token1_symbol, amount0, amount1, amount_usd, fee_tier, network
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING;
                """
                data = [
                    (
                        s['id'],
                        datetime.fromtimestamp(s['timestamp'], timezone.utc),
                        s['tx_hash'],
                        s['token0_address'],
                        s['token1_address'],
                        s['token0_symbol'],
                        s['token1_symbol'],
                        s['amount0'],
                        s['amount1'],
                        s['amountUSD'],
                        s['fee_tier'],
                        network
                    ) for s in swaps
                ]
                cur.executemany(insert_query, data)
            conn.commit()

    def get_last_swap_timestamp(self, network: str = "Ethereum") -> Optional[int]:
        """
        Get the timestamp of the latest swap stored in the database.
        Returns None if the table is empty.
        """
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(timestamp) FROM uniswap_v4_swaps WHERE network = %s", (network,))
                res = cur.fetchone()
                if res and res[0]:
                    return int(res[0].timestamp())
        return None
