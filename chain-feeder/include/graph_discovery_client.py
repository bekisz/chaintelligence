import requests
import logging
import os
import math
from typing import List, Dict, Any

from include.v4_pool import derive_v4_pool_id

logger = logging.getLogger(__name__)

# Uniswap V3 Subgraph IDs on The Graph Decentralized Network
UNISWAP_V3_SUBGRAPH_IDS = {
    "Ethereum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "3V7ZY6muhxaQL5qvntX1CFXJ32W7BxXZTGTwmpH5J4t3",
    "Base": "HMuAwufqZ1YCRmzL2SfHTVkzZovC9VL2UAKhjvRqKiR1",
    "Polygon": "3hCPRGf4z88VC5rsBKU5AA9FBBq5nF3jbKJG7VZCbhjm",
}

# Uniswap V4 Subgraph IDs (Decentralized Network)
UNISWAP_V4_SUBGRAPH_IDS = {
    # Validated Ethereum Mainnet Subgraph
    "Ethereum": "DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G",
    # Arbitrum/Base IDs to be verified/updated
    "Arbitrum": "G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r", 
    "Base": "Gqm2b5J85n1bhCyDMpGbtbVn4935EvvdyHdHrx3dibyj",
}

UNISWAP_V4_URLS = {
    # Gateway URLs
    "Ethereum": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G",
    "Arbitrum": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r",
    "Base": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/Gqm2b5J85n1bhCyDMpGbtbVn4935EvvdyHdHrx3dibyj",
}

UNISWAP_V3_URLS = {
    "Ethereum": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/3V7ZY6muhxaQL5qvntX1CFXJ32W7BxXZTGTwmpH5J4t3",
    "Base": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/HMuAwufqZ1YCRmzL2SfHTVkzZovC9VL2UAKhjvRqKiR1",
    "Polygon": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/3hCPRGf4z88VC5rsBKU5AA9FBBq5nF3jbKJG7VZCbhjm",
}

DISCOVERY_QUERY_V3 = """
query GetWalletPositions($owner: String!) {
  positions(where: { owner: $owner, liquidity_gt: 0 }) {
    id
    owner
    pool {
      id
      token0 {
        symbol
        decimals
        id
      }
      token1 {
        symbol
        decimals
        id
      }
      feeTier
      tick
    }
    tickLower {
      tickIdx
    }
    tickUpper {
      tickIdx
    }
    liquidity
  }
}
"""

DISCOVERY_QUERY_V4 = """
query GetWalletPositionsV4($owner: String!) {
  positions(where: { owner: $owner }) {
    transfers {
      tokenId
    }
  }
}
"""

POOL_RESOLUTION_QUERY_V3 = """
query GetPoolByTokens($token0: String!, $token1: String!, $fee: String!) {
  pools(where: { token0: $token0, token1: $token1, feeTier: $fee }) {
    id
  }
}
"""

POOL_RESOLUTION_QUERY_V4 = """
query GetV4Pool($t0_list: [String!], $t1_list: [String!], $fee: String!) {
  pools(where: { 
    token0_in: $t0_list, 
    token1_in: $t1_list, 
    feeTier: $fee 
  }, orderBy: totalValueLockedUSD, orderDirection: desc) {
    id
    totalValueLockedUSD
  }
}
"""

POOL_DISCOVERY_QUERY_V3 = """
query GetPoolPositions($pool: String!) {
  positions(where: { pool: $pool, liquidity_gt: 0 }, orderBy: liquidity, orderDirection: desc, first: 100) {
    id
    owner
    pool {
      id
      token0 { symbol, decimals, id }
      token1 { symbol, decimals, id }
      feeTier
      tick
    }
    tickLower { tickIdx }
    tickUpper { tickIdx }
    liquidity
  }
}
"""

POOL_DISCOVERY_QUERY_V4 = """
query GetPoolLPs($pool: String!) {
  pool(id: $pool) {
    token0 { symbol, id, decimals }
    token1 { symbol, id, decimals }
    tick
    feeTier
    totalValueLockedUSD
    volumeUSD
    modifyLiquiditys(first: 1000, orderBy: timestamp, orderDirection: desc) {
      origin
      sender
      amount
      tickLower
      tickUpper
      transaction {
        id
        transfers {
          tokenId
          to
        }
      }
    }
  }
}
"""

# Query to find all modifications for a specific pool and tick range (to catch non-NFT-transfer removals)
POOL_RANGE_MODS_QUERY = """
query GetRangeMods($pool: String!, $tickLower: Int!, $tickUpper: Int!) {
  modifyLiquidities(where: { 
    pool: $pool, 
    tickLower: $tickLower, 
    tickUpper: $tickUpper 
  }, first: 1000, orderBy: timestamp, orderDirection: desc) {
    amount
    timestamp
    origin
  }
}
"""

def verify_v4_position_rpc(token_id, network="Ethereum", session=None):
    """
    Calls getPoolAndPositionInfo on the PositionManager to get LIVE data.
    Returns (liquidity, owner, pool_key_dict, tick_range_dict) if valid, else (0, None, None, None).
    """
    pm_address = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
    # Selector for getPoolAndPositionInfo(uint256)
    selector = "0x7ba03aad"
    calldata = selector + format(int(token_id), '064x')
    
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pm_address, "data": calldata}, "latest"], "id": 1}
    
    # Try each RPC in the pool
    env_rpcs = os.getenv(f"RPC_URL_{network.upper()}")
    if not env_rpcs: env_rpcs = os.getenv("RPC_URL")
    
    rpc_pool = []
    if env_rpcs:
        rpc_pool = [r.strip() for r in env_rpcs.split(",") if r.strip()]
    
    if not rpc_pool:
        rpc_pool = ["https://eth.llamarpc.com"] if network == "Ethereum" else ["https://arbitrum.llamarpc.com"]

    req = session if session else requests

    for rpc_url in rpc_pool:
        try:
            resp = req.post(rpc_url, json=payload, timeout=5)
            if resp.status_code != 200: continue
            
            data = resp.json()
            res = data.get("result")
            if not res or res == "0x" or len(res) < 384:
                continue
                
            raw = res[2:]
            words = [raw[i:i+64] for i in range(0, len(raw), 64)]
            
            # Word 5 packing: PoolId (25 bytes? Actually 20 bytes visible) + TickUpper (3 bytes) + TickLower (3 bytes)
            w5 = words[5]
            
            def parse_i24(h):
                v = int(h, 16)
                return v - (1 << 24) if v >= (1 << 23) else v

            tu = parse_i24(w5[50:56])
            tl = parse_i24(w5[56:62])
            
            # Get actual liquidity via getPositionLiquidity(uint256)
            liq_payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pm_address, "data": "0x1efeed33" + format(int(token_id), '064x')}, "latest"], "id": 3}
            liq_resp = req.post(rpc_url, json=liq_payload, timeout=5).json()
            liq_res = liq_resp.get("result", "0x0")
            liq = int(liq_res, 16) if liq_res != "0x" else 0
            
            # Second call for ownerOf to be certain of current controller
            owner_payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pm_address, "data": "0x6352211e" + format(int(token_id), '064x')}, "latest"], "id": 2}
            owner_resp = req.post(rpc_url, json=owner_payload, timeout=5).json()
            owner = "0x" + owner_resp.get("result", "")[-40:]
            
            pool_key = {
                "token0": "0x" + words[0][-40:],
                "token1": "0x" + words[1][-40:],
                "fee": int(words[2], 16),
                "tickSpacing": int(words[3], 16),
                "hooks": "0x" + words[4][-40:]
            }
            
            return liq, owner, pool_key, {"tick_lower": tl, "tick_upper": tu}
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Exception in verify_v4_position_rpc: {e}")
            continue
            
    return 0, None, None, None

def fetch_token_metadata_rpc(addr, network="Ethereum", session=None):
    if addr.lower() == "0x0000000000000000000000000000000000000000":
        return "ETH", 18
        
    SEL_SYMBOL = "0x95d89b41"
    SEL_DECIMALS = "0x313ce567"
    
    env_rpcs = os.getenv(f"RPC_URL_{network.upper()}")
    if not env_rpcs: env_rpcs = os.getenv("RPC_URL")
    rpc_pool = []
    if env_rpcs:
        rpc_pool = [r.strip() for r in env_rpcs.split(",") if r.strip()]
    if not rpc_pool:
        rpc_pool = ["https://eth.llamarpc.com"] if network == "Ethereum" else ["https://arbitrum.llamarpc.com"]
        
    req = session if session else requests
    
    symbol = "UNK"
    decimals = 18
    
    # Get decimals
    for rpc_url in rpc_pool:
        try:
            payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": addr, "data": SEL_DECIMALS}, "latest"], "id": 1}
            r = req.post(rpc_url, json=payload, timeout=5).json()
            res = r.get("result")
            if res and res != "0x":
                decimals = int(res, 16)
                break
        except:
            continue
            
    # Get symbol
    for rpc_url in rpc_pool:
        try:
            payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": addr, "data": SEL_SYMBOL}, "latest"], "id": 2}
            r = req.post(rpc_url, json=payload, timeout=5).json()
            res = r.get("result")
            if res and res != "0x":
                raw = res[2:]
                length = int(raw[64:128], 16)
                data_hex = raw[128:128 + length*2]
                symbol = bytearray.fromhex(data_hex).decode('utf-8')
                break
        except:
            continue
            
    return symbol, decimals

def fetch_token_prices_llama(c0, c1, network="Ethereum", session=None):
    chain_map = {
        "Ethereum": "ethereum",
        "Arbitrum": "arbitrum",
        "Base": "base"
    }
    chain_id = chain_map.get(network, "ethereum")
    url = f"https://coins.llama.fi/prices/current/{chain_id}:{c0.lower()},{chain_id}:{c1.lower()}"
    req = session if session else requests
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = req.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("coins", {})
            p0 = data.get(f"{chain_id}:{c0.lower()}", {}).get("price", 0)
            p1 = data.get(f"{chain_id}:{c1.lower()}", {}).get("price", 0)
            return p0, p1
    except Exception as e:
        logger.warning(f"Llama price fetch failed: {e}")
    return 0, 0

def fetch_graph_positions(wallet_addresses: Any, networks: List[str] = None, graph_api_key: str = None) -> List[Dict[str, Any]]:
    """
    Finds all Uniswap V3 positions for a list of wallets across multiple networks using The Graph.
    
    Args:
        wallet_addresses: A single address (str) or a list of addresses.
        networks: List of networks to scan (default: all supported).
        graph_api_key: Graph API key.
        
    Returns:
        List of position dicts in a format compatible with the ingestion DAG.
    """
    if isinstance(wallet_addresses, str):
        wallets = [a.strip().lower() for a in wallet_addresses.split(',') if a.strip()]
    else:
        wallets = [a.lower() for a in wallet_addresses]

    if not networks:
        networks = list(UNISWAP_V3_URLS.keys())
        
    if not graph_api_key:
        graph_api_key = os.getenv("GRAPH_API_KEY")
        
    if not graph_api_key:
        logger.warning("No Graph API Key found. Discovery might fail or be heavily rate-limited.")

    all_positions = []
    headers = {"Content-Type": "application/json"}
    session = requests.Session()

    for network in networks:
        if network not in UNISWAP_V3_URLS:
            logger.warning(f"Network {network} not supported for Graph discovery.")
            continue
            
        endpoint = UNISWAP_V3_URLS[network].format(api_key=graph_api_key) if graph_api_key else UNISWAP_V3_URLS[network]
        
        # Redact API key for logs
        log_endpoint = endpoint.replace(graph_api_key, "*******") if graph_api_key else endpoint
        
        for wallet_lower in wallets:
            try:
                logger.info(f"🔍 Scanning {network} at {log_endpoint} for positions owned by {wallet_lower}...")
                response = session.post(
                    endpoint,
                    json={"query": DISCOVERY_QUERY_V3, "variables": {"owner": wallet_lower}},
                    headers=headers,
                    timeout=20
                )
                response.raise_for_status()
                data = response.json()
                
                if "errors" in data:
                    logger.error(f"GraphQL errors on {network} for {wallet_lower}: {data['errors']}")
                    continue
                    
                raw_positions = data.get("data", {}).get("positions", [])
                if raw_positions:
                    logger.info(f"✅ Found {len(raw_positions)} active V3 positions on {network} for {wallet_lower}.")
                
                for pos in raw_positions:
                    pool = pos.get("pool", {})
                    t0 = pool.get("token0", {})
                    t1 = pool.get("token1", {})
                    
                    token0_symbol = t0.get("symbol", "UNKNOWN")
                    token1_symbol = t1.get("symbol", "UNKNOWN")
                    token_id = pos.get("id")
                    
                    tl_obj = pos.get("tickLower")
                    tu_obj = pos.get("tickUpper")
                    
                    tick_lower = 0
                    if isinstance(tl_obj, dict): tick_lower = int(tl_obj.get("tickIdx", 0))
                    elif tl_obj is not None: tick_lower = int(tl_obj)
                    
                    tick_upper = 0
                    if isinstance(tu_obj, dict): tick_upper = int(tu_obj.get("tickIdx", 0))
                    elif tu_obj is not None: tick_upper = int(tu_obj)

                    normalized_pos = {
                        "protocol": "Uniswap V3",
                        "network": network,
                        "address": wallet_lower,
                        "position_label": f"{token0_symbol} / {token1_symbol} (Token ID: {token_id})",
                        "pool_address": pool.get("id"),
                        "fee_tier": pool.get("feeTier"),
                        "tick_lower": tick_lower,
                        "tick_upper": tick_upper,
                        "current_tick": int(pool.get("tick", 0)),
                        "liquidity": pos.get("liquidity", "0"),
                        "assets": [
                            {
                                "symbol": token0_symbol,
                                "address": t0.get("id"),
                                "decimals": int(t0.get("decimals", 18)),
                                "balance": 0,
                            },
                            {
                                "symbol": token1_symbol,
                                "address": t1.get("id"),
                                "decimals": int(t1.get("decimals", 18)),
                                "balance": 0,
                            }
                        ],
                        "unclaimed": [],
                        "balance_usd": 0,
                        "position_key": f"uniswapv3-{network}-{token_id}",
                        "images": []
                    }
                    all_positions.append(normalized_pos)
                    
            except Exception as e:
                logger.error(f"Failed to scan {network} for {wallet_lower} via Graph: {e}")

        # --- V4 Discovery ---
        # Only process V4 if URL exists
        if network in UNISWAP_V4_URLS:
            endpoint_v4 = UNISWAP_V4_URLS[network].format(api_key=graph_api_key) if graph_api_key else UNISWAP_V4_URLS[network]
            log_endpoint_v4 = endpoint_v4.replace(graph_api_key, "*******") if graph_api_key else endpoint_v4

            for wallet_lower in wallets:
                try:
                    logger.info(f"🔍 Scanning {network} V4 at {log_endpoint_v4}...")
                    response = session.post(
                        endpoint_v4,
                        json={"query": DISCOVERY_QUERY_V4, "variables": {"owner": wallet_lower}},
                        headers=headers,
                        timeout=20
                    )
                    # Don't raise immediately, check status
                    if response.status_code != 200:
                        logger.warning(f"V4 Scan failed with status {response.status_code}")
                        continue
                        
                    data = response.json()
                    if "errors" in data:
                        # V4 might not exist on all chains or schemas differ
                        logger.debug(f"V4 GraphQL errors on {network}: {data['errors']}")
                        continue

                    raw_v4 = data.get("data", {}).get("positions", [])
                    if raw_v4:
                        logger.info(f"✅ Found {len(raw_v4)} V4 positions on {network} for {wallet_lower}.")

                    for pos in raw_v4:
                        # Iterate all transfers/modifications to calculate liquidity
                        transfers = pos.get("transfers", [])
                        if not transfers: continue
                        
                        # 1. Collect all tokenIds seen in this wallet's transfer history
                        unique_token_ids = set()
                        for t in transfers:
                            tid = t.get("tokenId")
                            if tid: unique_token_ids.add(tid)
                        
                        # 2. Verify each Token ID via RPC (State checking)
                        for tid in unique_token_ids:
                            try:
                                liq, owner, pkey, trange = verify_v4_position_rpc(tid, network=network, session=session)
                                
                                # Skip if closed or owned by someone else now
                                if liq <= 1000000 or (owner and owner.lower() != wallet_lower):
                                    continue
                                
                                # Fetch token details from RPC data
                                t0_addr = pkey["token0"]
                                t1_addr = pkey["token1"]
                                # Derive the real V4 poolId (bytes32) — what the
                                # Uniswap explorer expects. pkey carries the
                                # authoritative fee/tickSpacing/hooks from the
                                # PoolManager, so this matches on-chain exactly
                                # (incl. native ETH and hooked pools). Fall back
                                # to the synthetic composite only if derivation
                                # fails, so the pool is never lost.
                                try:
                                    pool_addr = derive_v4_pool_id(
                                        t0_addr, t1_addr,
                                        pkey["fee"], pkey["tickSpacing"],
                                        pkey.get("hooks"),
                                    )
                                except Exception as e:
                                    logger.warning(f"V4 poolId derivation failed for {t0_addr}/{t1_addr}: {e}")
                                    pool_addr = f"v4-{t0_addr}-{t1_addr}"
                                
                                # Fetch token details from Graph / Fallback to RPC
                                t0_sym, t0_dec = "UNK", 18
                                t1_sym, t1_dec = "UNK", 18
                                
                                if t0_addr.lower() == "0x0000000000000000000000000000000000000000":
                                    t0_sym, t0_dec = "ETH", 18
                                if t1_addr.lower() == "0x0000000000000000000000000000000000000000":
                                    t1_sym, t1_dec = "ETH", 18
                                    
                                try:
                                    t_query = f"""
                                    query {{
                                      t0: token(id: "{t0_addr.lower()}") {{ symbol, decimals }}
                                      t1: token(id: "{t1_addr.lower()}") {{ symbol, decimals }}
                                    }}
                                    """
                                    t_resp = session.post(endpoint, json={"query": t_query}, timeout=5).json()
                                    if "data" in t_resp:
                                        if t_resp["data"].get("t0") and t0_sym == "UNK":
                                            t0_sym = t_resp["data"]["t0"].get("symbol", "UNK")
                                            t0_dec = int(t_resp["data"]["t0"].get("decimals", 18))
                                        if t_resp["data"].get("t1") and t1_sym == "UNK":
                                            t1_sym = t_resp["data"]["t1"].get("symbol", "UNK")
                                            t1_dec = int(t_resp["data"]["t1"].get("decimals", 18))
                                except Exception as e:
                                    logger.warning(f"Failed to fetch token symbols via Graph: {e}")
                                    
                                # Fallback to RPC for any remaining UNKs
                                if t0_sym == "UNK":
                                    t0_sym, t0_dec = fetch_token_metadata_rpc(t0_addr, network=network, session=session)
                                if t1_sym == "UNK":
                                    t1_sym, t1_dec = fetch_token_metadata_rpc(t1_addr, network=network, session=session)
                                
                                # Calculate realistic current tick using token prices
                                p0_usd, p1_usd = fetch_token_prices_llama(t0_addr, t1_addr, network=network, session=session)
                                current_tick = 0
                                if p0_usd > 0 and p1_usd > 0:
                                    try:
                                        ratio = p0_usd / p1_usd
                                        raw_price = ratio * (10**(t1_dec - t0_dec))
                                        if raw_price > 0:
                                            current_tick = int(math.log(raw_price) / math.log(1.0001))
                                    except Exception as e:
                                        logger.warning(f"Failed to calculate tick from price: {e}")
                                        current_tick = (trange["tick_lower"] + trange["tick_upper"]) // 2
                                else:
                                    current_tick = (trange["tick_lower"] + trange["tick_upper"]) // 2
                                
                                normalized_pos = {
                                    "protocol": "Uniswap V4", 
                                    "network": network,
                                    "address": wallet_lower,
                                    "position_label": f"V4 Position (Token ID: {tid})",
                                    "pool_address": pool_addr, 
                                    "fee_tier": pkey["fee"],
                                    "tick_lower": trange["tick_lower"],
                                    "tick_upper": trange["tick_upper"],
                                    "current_tick": current_tick,
                                    "liquidity": str(liq),
                                    "assets": [
                                        {"symbol": t0_sym, "address": t0_addr, "decimals": t0_dec, "balance": 0},
                                        {"symbol": t1_sym, "address": t1_addr, "decimals": t1_dec, "balance": 0}
                                    ],
                                    "position_key": f"uniswapv4-{network}-{tid}",
                                    "balance_usd": 0
                                }
                                all_positions.append(normalized_pos)
                            except: continue
                except Exception as e:
                    logger.error(f"Failed V4 scan on {network}: {e}")

    return all_positions

def fetch_positions_by_pool(pool_address: str, network: str = "Ethereum", protocol: str = "Uniswap V3", graph_api_key: str = None) -> List[Dict[str, Any]]:
    """
    Finds top LP positions for a specific pool address using The Graph.
    Supports both Uniswap V3 and V4.
    """
    if not graph_api_key:
        graph_api_key = os.getenv("GRAPH_API_KEY")
        
    if protocol == "Uniswap V3":
        endpoint = UNISWAP_V3_URLS.get(network)
        query = POOL_DISCOVERY_QUERY_V3
    elif protocol == "Uniswap V4":
        endpoint = UNISWAP_V4_URLS.get(network)
        query = POOL_DISCOVERY_QUERY_V4
    else:
        logger.error(f"Protocol {protocol} not supported for pool discovery.")
        return []
        
    if not endpoint:
        logger.error(f"Network {network} not supported for {protocol} discovery.")
        return []
        
    endpoint = endpoint.format(api_key=graph_api_key) if graph_api_key else endpoint
    headers = {"Content-Type": "application/json"}
    
    try:
        logger.info(f"🔍 Searching Graph ({protocol}) for positions in pool {pool_address} on {network}...")
        response = requests.post(
            endpoint,
            json={"query": query, "variables": {"pool": pool_address.lower()}},
            headers=headers,
            timeout=20
        )
        response.raise_for_status()
        data = response.json()
        
        if "errors" in data:
            logger.error(f"GraphQL errors for pool {pool_address}: {data['errors']}")
            return []
            
        all_positions = []
        
        if protocol == "Uniswap V3":
            raw_positions = data.get("data", {}).get("positions", [])
            for pos in raw_positions:
                pool = pos.get("pool", {})
                t0 = pool.get("token0", {})
                t1 = pool.get("token1", {})
                
                token0_symbol = t0.get("symbol", "UNKNOWN")
                token1_symbol = t1.get("symbol", "UNKNOWN")
                token_id = pos.get("id")
                
                normalized_pos = {
                    "protocol": "Uniswap V3",
                    "network": network,
                    "address": pos.get("owner", "").lower(),
                    "position_label": f"{token0_symbol} / {token1_symbol} (Token ID: {token_id})",
                    "pool_address": pool_address.lower(),
                    "fee_tier": pool.get("feeTier"),
                    "tick_lower": int(pos.get("tickLower", {}).get("tickIdx", 0)),
                    "tick_upper": int(pos.get("tickUpper", {}).get("tickIdx", 0)),
                    "current_tick": int(pool.get("tick", 0)),
                    "liquidity": pos.get("liquidity", "0"),
                    "assets": [
                        {"symbol": token0_symbol, "address": t0.get("id"), "decimals": int(t0.get("decimals", 18)), "balance": 0},
                        {"symbol": token1_symbol, "address": t1.get("id"), "decimals": int(t1.get("decimals", 18)), "balance": 0}
                    ],
                    "position_key": f"uniswapv3-{network}-{token_id}",
                    "balance_usd": 0
                }
                all_positions.append(normalized_pos)
        
        elif protocol == "Uniswap V4":
            pool_data = data.get("data", {}).get("pool", {})
            if not pool_data: return []
            
            # For a pool scan, we look at all recent modifications to find potential candidates
            # but we ALWAYS verify current state via RPC to avoid SUM-errors or double-counting.
            mods = pool_data.get("modifyLiquiditys", [])
            potential_tids = set()
            for m in mods:
                transfers = m.get("transaction", {}).get("transfers", [])
                for t in transfers:
                    if t.get("tokenId"): potential_tids.add(t.get("tokenId"))
            
            for tid in potential_tids:
                try:
                    liq, owner, pkey, trange = verify_v4_position_rpc(tid, network=network)
                    if liq <= 1000000: continue # Inactive or Dust
                    
                    normalized_pos = {
                        "protocol": "Uniswap V4",
                        "network": network,
                        "address": owner.lower() if owner else "unknown",
                        "position_label": f"V4 Position (Token ID: {tid})",
                        "pool_address": pool_address.lower(),
                        "fee_tier": pkey["fee"],
                        "tick_lower": trange["tick_lower"],
                        "tick_upper": trange["tick_upper"],
                        "current_tick": int(pool_data.get("tick", 0)),
                        "liquidity": str(liq),
                        "assets": [
                            {"symbol": "UNK", "address": pkey["token0"], "decimals": 18, "balance": 0},
                            {"symbol": "UNK", "address": pkey["token1"], "decimals": 18, "balance": 0}
                        ],
                        "position_key": f"uniswapv4-{network}-{tid}",
                        "balance_usd": 0
                    }
                    all_positions.append(normalized_pos)
                except: continue
                
        return all_positions
    except Exception as e:
        logger.error(f"Failed to fetch {protocol} pool positions for {pool_address}: {e}")
        return []

def resolve_pool_address(t0_addr: str, t1_addr: str, fee_tier: str, network: str = "Ethereum", protocol: str = "Uniswap V3", graph_api_key: str = None) -> str:
    """
    Attempts to find a pool ID on The Graph using token addresses and fee tier.
    """
    if not graph_api_key:
        graph_api_key = os.getenv("GRAPH_API_KEY")
        
    if protocol == "Uniswap V3":
        endpoint = UNISWAP_V3_URLS.get(network)
        query = POOL_RESOLUTION_QUERY_V3
    elif protocol == "Uniswap V4":
        endpoint = UNISWAP_V4_URLS.get(network)
        query = POOL_RESOLUTION_QUERY_V4
    else:
        return None
        
    if not endpoint:
        return None
        
    endpoint = endpoint.format(api_key=graph_api_key) if graph_api_key else endpoint
    
    # Clean fee tier (ensure it's just the number)
    fee = str(fee_tier)
    if '%' in fee: fee = "".join(filter(str.isdigit, fee))

    # WETH and Native ETH addresses
    WETH_ADDR = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    NATIVE_ETH_ADDR = "0x0000000000000000000000000000000000000000"
    EVM_ETH_ADDR = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"  # Common alias for native ETH
    
    t0_list = [t0_addr.lower()]
    t1_list = [t1_addr.lower()]

    if protocol == "Uniswap V4":
        # Map WETH and the 0xeeee... ETH alias to V4's native ETH address (0x0000...)
        for eth_alias in [WETH_ADDR, EVM_ETH_ADDR]:
            if t0_addr.lower() == eth_alias: t0_list.append(NATIVE_ETH_ADDR)
            if t1_addr.lower() == eth_alias: t1_list.append(NATIVE_ETH_ADDR)
        
        # In V4 order can be any way, but let's just supply both lists to BOTH token slots
        # The subgraph where filter { token0_in, token1_in } will match regardless of order 
        # if we supply the full set to both.
        combined = list(set(t0_list + t1_list))
        
        try:
            logger.info(f"🔍 Resolving {protocol} pool for {t0_addr}-{t1_addr} ({fee}) on {network}...")
            response = requests.post(
                endpoint,
                json={
                    "query": query, 
                    "variables": {"t0_list": combined, "t1_list": combined, "fee": fee}
                },
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            response.raise_for_status()
            data = response.json()
            
            pools = data.get("data", {}).get("pools", [])
            if pools:
                resolved_id = pools[0].get("id")
                logger.info(f"✅ Resolved most active {protocol} pool: {resolved_id} (TVL: {pools[0].get('totalValueLockedUSD', '0')})")
                return resolved_id
        except Exception as e:
            logger.error(f"Resolution failed for {t0_addr}-{t1_addr} ({protocol}): {e}")
            
    else:
        # Standard V3 strictly ordered query
        a0, a1 = (t0_addr.lower(), t1_addr.lower())
        if a0 > a1: a0, a1 = a1, a0
        try:
            response = requests.post(
                endpoint,
                json={
                    "query": query, 
                    "variables": {"token0": a0, "token1": a1, "fee": fee}
                },
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            response.raise_for_status()
            data = response.json()
            pools = data.get("data", {}).get("pools", [])
            if pools:
                return pools[0].get("id")
        except Exception as e:
            logger.error(f"Resolution failed for {t0_addr}-{t1_addr}: {e}")

    return None

    return all_positions

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    test_wallets = os.getenv("TARGET_ADDRESS", "")
    if test_wallets:
        res = fetch_graph_positions(test_wallets)
        print(json.dumps(res, indent=2))
    else:
        print("Set TARGET_ADDRESS env var to run test.")
