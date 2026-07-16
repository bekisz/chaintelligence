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

def load_token_addresses_for_chain(chain: str) -> Dict[str, str]:
    """Load symbol → contract_address mapping for a given chain from DB."""
    # Normalize the network name used internally ("BNB") to the canonical
    # chain name stored in coin_contract.chain ("bsc"). Without this, the
    # BNB fetchers (PancakeSwap V3 / V4) resolve 0 tracked token addresses
    # and ingest nothing.
    chain = 'bsc' if chain.lower() == 'bnb' else chain.lower()
    mapping = {}
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        cur.execute("""
            SELECT UPPER(c.symbol), cc.contract_address
            FROM coin_contract cc
            JOIN coin c ON cc.coin_id = c.coin_id
            WHERE LOWER(cc.chain) = %s
        """, (chain,))
        for row in cur.fetchall():
            mapping[row[0]] = row[1]
        cur.close()
        conn.close()
    except Exception as e:
        # Static fallbacks for testing/safety if DB is unavailable
        if chain.lower() == 'arbitrum':
            return {
                'ETH':  '0x0000000000000000000000000000000000000000',
                'USDC.e': '0xff970a61a04b1ca14834a43f5de4533ebddb5cc8',
                'USDC': '0xaf88d065e77c8cc2239327c5edb3a432268e5831',
                'WETH': '0x82af49447d8a07e3bd95bd0d56f35241523fbab1',
                'USDT': '0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9',
                'WBTC': '0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f',
                'DAI':  '0xda10009c55681e77d502082691d29f8fb095569f',
                'LINK': '0xf97f4df75117a78c1a5a0dbb814af92458539fb4',
                'GMX':  '0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a',
                'AAVE': '0xba5ddd1f9d7f570dc94a51479a000e3bce967196',
                'ZRO':  '0x6985884c4392d348587b19cb9eaaf157f13271cd',
            }
        elif chain.lower() == 'base':
            return {
                'ETH':   '0x0000000000000000000000000000000000000000',
                'WETH':  '0x4200000000000000000000000000000000000006',
                'USDC':  '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
                'USDbC': '0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA',
                'cbBTC': '0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf',
            }
        elif chain.lower() == 'bsc':
            return {
                'WBNB':  '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',
                'USDC':  '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d',
                'USDT':  '0x55d398326f99059ff775485246999027b3197955',
            }
        elif chain.lower() == 'ethereum':
            return {
                'USDC': '0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eB48',
                'USDT': '0xdAC17F958D2ee523a2206206994597c13d831ec7',
                'WETH': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
                'WBTC': '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599',
            }
    return mapping

class UniswapV3Fetcher:
    def __init__(self, verbose: bool = False, network: str = "Ethereum", protocol: str = "Uniswap V3"):
        self.verbose = verbose
        self.network = network
        self.protocol = protocol
        self.session = requests.Session()

        # Build network-aware V3 and V4 URLs
        import os
        GRAPH_API_KEY = os.getenv('GRAPH_API_KEY', '')
        if self.network == "BNB" and self.protocol == "PancakeSwap V3":
            v3_subgraph_id = "ChmxqA9bX71cB2cQTRRULbWUBKoMRk7oh3JnpZShDQ2V"
            v4_subgraph_id = "7XgdLW3bts4HktCYsu9dy8bEnuiNeZuftcuK3Aj4JXYV"  # unused (V3 protocol)
        elif self.network == "BNB" and self.protocol == "PancakeSwap V4":
            # PancakeSwap V4 (Infinity) BNB subgraph on The Graph Decentralized
            # Network. Schema is Uniswap-V4-like (swaps: token0/token1/amount0/
            # amount1/amountUSD/pool.feeTier; id = "{txhash}-{logIndex}"), so the
            # V4 query + normalization branches below handle it unchanged.
            # Verified via scratch/probe_pancakeswap_v4_subgraph.py.
            v3_subgraph_id = "7XgdLW3bts4HktCYsu9dy8bEnuiNeZuftcuK3Aj4JXYV"  # unused (V4 protocol)
            v4_subgraph_id = "7XgdLW3bts4HktCYsu9dy8bEnuiNeZuftcuK3Aj4JXYV"
        elif self.network == "Arbitrum":
            v3_subgraph_id = "FbCGRftH4a3yZugY7TnbYgPJVEv2LvMT6oF1fxPe9aJM"  # Uniswap V3 Arbitrum swaps (verified)
            v4_subgraph_id = "G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"  # Uniswap V4 Arbitrum swaps
        elif self.network == "Base" and self.protocol == "Aerodrome":
            # Aerodrome Slipstream (concentrated-liquidity, V3-fork) subgraph on
            # The Graph Decentralized Network. Schema is Uniswap-V3-identical
            # (swaps: id="{tx}#{logIndex}", token0/1, amount0/1, amountUSD,
            # pool{feeTier}), so the V3 query + normalization below are reused
            # unchanged. Verified via scratch/probe_aerodrome_subgraph.py.
            # NB: this is Slipstream (V2-CL), NOT Aerodrome V1 (Velodrome-fork);
            # the V1 subgraph is not on this gateway. Pool addresses are NOT
            # CREATE2-derivable with the V3 factory/init-hash, so the API
            # enrichment path skips derivation for protocol='Aerodrome'.
            v3_subgraph_id = "GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM"
            v4_subgraph_id = "GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM"  # unused (V3 protocol)
        elif self.network == "Base":
            v3_subgraph_id = "43Hwfi3dJSoGpyas9VwNoDAv55yjgGrPpNSmbQZArzMG"
            v4_subgraph_id = "FUbEPQw1oMghy39fwWBFY5fE6MXPXZQtjncQy2cXdrNS" # Placeholder if V4 is not available
        elif self.network == "BNB":
            v3_subgraph_id = "7XgdLW3bts4HktCYsu9dy8bEnuiNeZuftcuK3Aj4JXYV"
            v4_subgraph_id = "7XgdLW3bts4HktCYsu9dy8bEnuiNeZuftcuK3Aj4JXYV" # Placeholder
        else: # Ethereum
            v3_subgraph_id = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
            v4_subgraph_id = "DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

        if not GRAPH_API_KEY or GRAPH_API_KEY == 'YOUR_GRAPH_API_KEY':
            self.subgraph_url = f'https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/{v3_subgraph_id}'
            self.subgraph_v4_url = f'https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/{v4_subgraph_id}'
        else:
            self.subgraph_url = f'https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{v3_subgraph_id}'
            self.subgraph_v4_url = f'https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{v4_subgraph_id}'
        
        # Load token addresses dynamically
        self.token_addresses = load_token_addresses_for_chain(self.network)
    
    def _log(self, message: str):
        if self.verbose:
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}")
    
    def _build_swap_query(self, start_timestamp: int, end_timestamp: int, filter_field: str, filter_values: List[str]) -> str:
        addr_list = str(filter_values).replace("'", '"')
        if self.protocol == "PancakeSwap V3":
            messari_field = "tokenIn_in" if filter_field == "token0_in" else "tokenOut_in"
            query = f"""
            {{
              swaps(
                first: {MAX_RESULTS_PER_QUERY}
                orderBy: timestamp
                orderDirection: asc
                where: {{
                  timestamp_gte: {start_timestamp}
                  timestamp_lte: {end_timestamp}
                  {messari_field}: {addr_list}
                }}
              ) {{
                id
                hash
                timestamp
                tokenIn {{ id symbol }}
                tokenOut {{ id symbol }}
                amountIn
                amountOut
                amountInUSD
                pool {{
                  id
                  name
                  inputTokens {{ id symbol }}
                  fees {{ feePercentage }}
                }}
              }}
            }}
            """
            return query

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
            pool {{ id feeTier }}
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

        addr_to_sym = {addr.lower(): sym for sym, addr in self.token_addresses.items()}

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

                if self.protocol == "PancakeSwap V3":
                    token0 = swap.get('tokenIn') or {}
                    token1 = swap.get('tokenOut') or {}
                    tx_hash = swap.get('hash', 'unknown')
                    t0_addr = token0.get('id', '').lower()
                    t1_addr = token1.get('id', '').lower()
                    
                    pool = swap.get('pool') or {}
                    pool_name = pool.get('name', '')
                    fee_tier_str = "0.25%"
                    import re
                    match = re.search(r'(\d+(?:\.\d+)?%)', pool_name)
                    if match:
                        fee_tier_str = match.group(1)
                    else:
                        fees_list = pool.get('fees', [])
                        if fees_list:
                            try:
                                fee_pct = float(fees_list[-1].get('feePercentage', 0))
                                fee_tier_str = f"{fee_pct:g}%"
                            except: pass
                    
                    pool_tokens = pool.get('inputTokens', [])
                    if len(pool_tokens) >= 2:
                        p0_addr = pool_tokens[0].get('id', '').lower()
                        p1_addr = pool_tokens[1].get('id', '').lower()
                    else:
                        p0_addr = t0_addr
                        p1_addr = t1_addr

                    amount_in_val = to_float(swap.get('amountIn'))
                    amount_out_val = to_float(swap.get('amountOut'))

                    normalized = {
                        'id': swap_id,
                        'timestamp': to_int(swap.get('timestamp')),
                        'tx_hash': tx_hash,
                        'token0_address': p0_addr,
                        'token1_address': p1_addr,
                        'token0_symbol': addr_to_sym.get(p0_addr, pool_tokens[0].get('symbol') if len(pool_tokens) >= 1 else 'UNKNOWN').upper(),
                        'token1_symbol': addr_to_sym.get(p1_addr, pool_tokens[1].get('symbol') if len(pool_tokens) >= 2 else 'UNKNOWN').upper(),
                        'amount0': amount_in_val if t0_addr == p0_addr else -amount_out_val,
                        'amount1': amount_out_val if t1_addr == p1_addr else -amount_in_val,
                        'amountUSD': to_float(swap.get('amountInUSD')),
                        'fee_tier': fee_tier_str
                    }
                else:
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
        
        addresses = [addr.lower() for addr in self.token_addresses.values()]
            
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
        t0, t1 = sorted([token0_addr.lower(), token1_addr.lower()])
        
        if self.protocol == "PancakeSwap V3":
            query = f"""
            {{
              liquidityPools(where: {{
                inputTokens_contains: ["{t0}", "{t1}"]
              }}) {{
                id
                dailySnapshots(
                    where: {{ timestamp_gte: {start_ts} }}
                    orderBy: timestamp
                    orderDirection: asc
                ) {{
                  timestamp
                  totalValueLockedUSD
                  dailyVolumeUSD
                  dailySwapCount
                }}
              }}
            }}
            """
            result = self._execute_query(query)
            if not result or 'data' not in result:
                return []
                
            pools = result['data'].get('liquidityPools', [])
            if not pools:
                return []
                
            pool_data = pools[0]
            day_datas = pool_data.get('dailySnapshots', [])
            
            normalized_data = []
            for d in day_datas:
                 normalized_data.append({
                     'date': datetime.fromtimestamp(int(d['timestamp']), timezone.utc).date(),
                     'tvl_usd': float(d.get('totalValueLockedUSD', 0) or 0),
                     'volume_usd': float(d.get('dailyVolumeUSD', 0) or 0),
                     'tx_count': int(d.get('dailySwapCount', 0) or 0),
                 })
                 
            return normalized_data

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

# ---------------------------------------------------------------------------
# Symbol → coin_id lookup for the unified swaps table
# Loaded at module init so DAG tasks have it in memory.
# ---------------------------------------------------------------------------
def _load_symbol_to_coin_id() -> Dict[str, int]:
    mapping = {}
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        cur.execute("SELECT UPPER(symbol), coin_id FROM coin WHERE coin_id IS NOT NULL")
        for row in cur.fetchall():
            mapping[row[0]] = row[1]
        cur.close()
        conn.close()
    except Exception:
        pass
    return mapping

SYMBOL_TO_COIN_ID = _load_symbol_to_coin_id()

def _compute_fee_bps(fee_tier: Optional[str]) -> Optional[float]:
    """Convert a fee tier string like '0.05%' or '3000' to fee_bps (5.0 or 30.0)."""
    if not fee_tier or fee_tier == 'Dynamic':
        return None
    try:
        fee_str = str(fee_tier).strip()
        fee_val = float(fee_str.replace('%', ''))
        if '%' not in fee_str and fee_val >= 10.0:
            return fee_val / 100.0
        return fee_val * 100.0
    except (ValueError, AttributeError):
        return None

class PostgresStorage:
    def __init__(self):
        self.conn_str = DATA_WAREHOUSE_DB

    def save_swaps(self, swaps: List[Dict], network: str = "Ethereum", protocol: str = "Uniswap V3"):
        if not swaps:
            return

        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name FROM chain")
                chain_map = {row[1].lower(): row[0] for row in cur.fetchall()}
                cur.execute("SELECT id, name FROM protocol")
                protocol_map = {row[1].lower(): row[0] for row in cur.fetchall()}
                
                chain_id = chain_map.get(network.lower())
                protocol_id = protocol_map.get(protocol.lower())
                if chain_id is None or protocol_id is None:
                    raise ValueError(f"Invalid network ({network}) or protocol ({protocol}) for lookup mappings")

                # Load pool lookup maps
                cur.execute("""
                    SELECT id, pool_id, chain_id, protocol_id, coin0_id, coin1_id, fee_bps
                    FROM liquidity_pool
                """)
                rows = cur.fetchall()
                pool_id_map = {row[1].lower(): row[0] for row in rows if row[1]}
                pool_tokens_map = {(row[2], row[3], frozenset({row[4], row[5]}), row[6]): row[0] for row in rows}

                insert_query = """
                INSERT INTO swaps (
                    tx_hash, log_index, ts, pool_id,
                    amount0, amount1, amount_usd
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ts, tx_hash, log_index) DO NOTHING;
                """
                
                data = []
                from collections import defaultdict
                tx_hash_counters = defaultdict(int)
                for s in swaps:
                    t0_id = SYMBOL_TO_COIN_ID.get(s.get('token0_symbol', '').upper())
                    t1_id = SYMBOL_TO_COIN_ID.get(s.get('token1_symbol', '').upper())
                    if t0_id is None or t1_id is None:
                        continue  # skip swaps for untracked tokens

                    # Extract log_index from the subgraph id
                    swap_id = s.get('id', '')
                    if not swap_id:
                        continue  # skip swaps with no id

                    log_index = None
                    if '#' in swap_id:
                        parts = swap_id.split('#')
                        if len(parts) > 1 and parts[1]:
                            try:
                                log_index = int(parts[1])
                            except ValueError:
                                pass
                    elif '-' in swap_id:
                        parts = swap_id.rsplit('-', 1)
                        if len(parts) > 1 and parts[1]:
                            try:
                                log_index = int(parts[1])
                            except ValueError:
                                pass
                    
                    if log_index is None:
                        tx_hash = s.get('tx_hash') or 'unknown'
                        log_index = tx_hash_counters[tx_hash]
                        tx_hash_counters[tx_hash] += 1

                    ts_val = datetime.fromtimestamp(s['timestamp'], timezone.utc)

                    fbps = _compute_fee_bps(s.get('fee_tier'))
                    
                    # 1. Match by on-chain pool ID
                    sg_pool_id = (s.get('pool') or {}).get('id')
                    pool_id = None
                    if sg_pool_id:
                        pool_id = pool_id_map.get(sg_pool_id.lower())
                    
                    # 2. Fall back to tokens/fee matching
                    if pool_id is None:
                        pool_id = pool_tokens_map.get((chain_id, protocol_id, frozenset({t0_id, t1_id}), fbps))
                        
                    if pool_id is None:
                        continue

                    data.append((
                        s['tx_hash'],
                        log_index,
                        ts_val,
                        pool_id,
                        s.get('amount0'),
                        s.get('amount1'),
                        s.get('amountUSD'),
                    ))

                if data:
                    cur.executemany(insert_query, data)
            conn.commit()

    def get_last_swap_timestamp(self, network: str = "Ethereum", protocol: str = "Uniswap V3") -> Optional[int]:
        with psycopg2.connect(self.conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT MAX(s.ts)
                    FROM swaps s
                    JOIN liquidity_pool lp ON s.pool_id = lp.id
                    JOIN chain c ON lp.chain_id = c.id
                    JOIN protocol p ON lp.protocol_id = p.id
                    WHERE LOWER(c.name) = LOWER(%s) AND LOWER(p.name) = LOWER(%s)
                """, (network, protocol))
                res = cur.fetchone()
                if res and res[0]:
                    return int(res[0].timestamp())
        return None

class PostgresStorageV4(PostgresStorage):
    """V4 storage — targets the same unified swaps table.

    Overrides save_swaps so the default protocol is 'Uniswap V4' (the parent
    defaults to 'Uniswap V3', which would mislabel V4 ingestion). Callers that
    pass an explicit protocol (e.g. PancakeSwap V4) override this default.
    """
    def save_swaps(self, swaps: List[Dict], network: str = "Ethereum", protocol: str = "Uniswap V4"):
        return super().save_swaps(swaps, network=network, protocol=protocol)


def to_checksum_address(address: str) -> str:
    from eth_hash.auto import keccak
    addr_lower = address.lower().replace('0x', '')
    if len(addr_lower) != 40:
        return address
    address_hash = keccak(addr_lower.encode('ascii')).hex()
    checksum_address = '0x' + ''.join(
        c.upper() if int(address_hash[i], 16) >= 8 else c 
        for i, c in enumerate(addr_lower)
    )
    return checksum_address


def derive_pool_identifiers(protocol: str, network: str, token0_addr: str, token1_addr: str, fee_bps: Optional[int], dex_config: dict) -> tuple:
    """Derive pool_address and pool_id for a pool.

    Returns (pool_address, pool_id) or (None, None).
    """
    from eth_hash.auto import keccak

    # Normalization keys
    chain_key = network.lower()
    if chain_key == 'bnb':
        chain_key = 'bsc'

    # Native token wrapped mapping
    WRAPPED_MAP = {
        ('ethereum', '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'): '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
        ('arbitrum', '0x0000000000000000000000000000000000000000'): '0x82af49447d8a07e3bd95bd0d56f35241523fbab1',
        ('base', '0x0000000000000000000000000000000000000000'): '0x4200000000000000000000000000000000000006',
        ('bnb', '0xa05ccd2f8ac92afe092a7240e948aa3e17cef843'): '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',
    }

    t0_addr = token0_addr.lower()
    t1_addr = token1_addr.lower()

    if (chain_key, t0_addr) in WRAPPED_MAP:
        t0_addr = WRAPPED_MAP[(chain_key, t0_addr)]
    if (chain_key, t1_addr) in WRAPPED_MAP:
        t1_addr = WRAPPED_MAP[(chain_key, t1_addr)]

    # Validate EVM address formats
    def is_valid(addr):
        addr_clean = addr.removeprefix('0x')
        if len(addr_clean) != 40:
            return False
        try:
            int(addr_clean, 16)
            return True
        except ValueError:
            return False

    if not is_valid(t0_addr) or not is_valid(t1_addr):
        return None, None

    # CREATE2 bytes parsing
    t0_bytes = bytes.fromhex(t0_addr.removeprefix('0x'))
    t1_bytes = bytes.fromhex(t1_addr.removeprefix('0x'))
    if t1_bytes < t0_bytes:
        t0_bytes, t1_bytes = t1_bytes, t0_bytes

    if protocol == 'Uniswap V2':
        cfg = dex_config.get('uniswap_v2', {}).get(chain_key)
        if cfg:
            salt = keccak(t0_bytes + t1_bytes)
            f_bytes = bytes.fromhex(cfg['factory'].removeprefix('0x'))
            ih_bytes = bytes.fromhex(cfg['init_hash'].removeprefix('0x'))
            derived = '0x' + keccak(b'\xff' + f_bytes + salt + ih_bytes)[12:].hex()
            addr = to_checksum_address(derived)
            return addr, addr
    elif protocol in ('Uniswap V3', 'PancakeSwap V3'):
        proto_key = 'uniswap_v3' if protocol == 'Uniswap V3' else 'pancakeswap_v3'
        cfg = dex_config.get(proto_key, {}).get(chain_key)
        if cfg:
            fee_val = int(fee_bps) if fee_bps is not None else 3000
            salt = keccak(b'\x00' * 12 + t0_bytes + b'\x00' * 12 + t1_bytes + fee_val.to_bytes(32, 'big'))
            f_bytes = bytes.fromhex(cfg['factory'].removeprefix('0x'))
            ih_bytes = bytes.fromhex(cfg['init_hash'].removeprefix('0x'))
            derived = '0x' + keccak(b'\xff' + f_bytes + salt + ih_bytes)[12:].hex()
            addr = to_checksum_address(derived)
            return addr, addr
    elif 'V4' in protocol:
        # V4 default hookless pool ID derivation
        fee_val = int(fee_bps) if fee_bps is not None else 100
        _V4_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}
        tick_spacing = _V4_TICK_SPACING.get(fee_val, 10)
        hooks = b'\x00' * 32
        enc = (t0_bytes.rjust(32, b'\x00') + t1_bytes.rjust(32, b'\x00') +
               fee_val.to_bytes(32, 'big') + tick_spacing.to_bytes(32, 'big', signed=True) + hooks)
        derived_id = '0x' + keccak(enc).hex()
        return None, derived_id

    return None, None
