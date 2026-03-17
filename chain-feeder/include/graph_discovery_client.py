import requests
import logging
import os
from typing import List, Dict, Any

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
                response = requests.post(
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
                    response = requests.post(
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
                        
                        total_liquidity = 0
                        pool_info = None
                        target_tick_lower = None
                        target_tick_upper = None
                        
                        # Flatten all modifyLiquiditys from all transactions
                        all_mods = []
                        for t in transfers:
                            tx = t.get("transaction", {})
                            all_mods.extend(tx.get("modifyLiquiditys", []))
                            
                        # Find the first valid modification to establish Pool/Ticks
                        for mod in all_mods:
                            if not pool_info and mod.get("pool"):
                                pool_info = mod.get("pool")
                                target_tick_lower = int(mod.get("tickLower", 0))
                                target_tick_upper = int(mod.get("tickUpper", 0))
                            
                            # Sum liquidity if ticks match the position's ticks
                            # Note: A transaction could hypothetically modify other positions in same pool
                            # But V4 subgraph structure makes it hard to distinguish without more info.
                            # Assuming broad match for now as typical user behavior is 1 position per pool per tx.
                            if pool_info and mod.get("pool") and mod.get("pool", {}).get("id") == pool_info.get("id"):
                                # Verify ticks match (Position is defined by Ticks in V4 mostly)
                                m_tl = int(mod.get("tickLower", 0))
                                m_tu = int(mod.get("tickUpper", 0))
                                if m_tl == target_tick_lower and m_tu == target_tick_upper:
                                    amount_str = mod.get("amount", "0")
                                    # Convert to signed int (though amount is usually positive for add?)
                                    # Wait, `amount` in ModifyLiquidity is `int256`.
                                    # Mint = positive, Burn = negative.
                                    try:
                                        total_liquidity += int(amount_str)
                                    except:
                                        pass

                        if not pool_info: continue

                        t0 = pool_info.get("token0", {})
                        t1 = pool_info.get("token1", {})
                        token0_symbol = t0.get("symbol", "UNKNOWN")
                        token1_symbol = t1.get("symbol", "UNKNOWN")
                        token_id = pos.get("tokenId")

                        normalized_pos = {
                            "protocol": "Uniswap V4", 
                            "network": network,
                            "address": wallet_lower,
                            "position_label": f"{token0_symbol} / {token1_symbol} (Token ID: {token_id})",
                            "pool_address": pool_info.get("id", f"v4-{token0_symbol}-{token1_symbol}"), 
                            "fee_tier": pool_info.get("feeTier"),
                            "tick_lower": target_tick_lower,
                            "tick_upper": target_tick_upper,
                            "current_tick": int(pool_info.get("tick", 0)),
                            "liquidity": str(total_liquidity), 
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
                            "position_key": f"uniswapv4-{network}-{token_id}",
                            "images": []
                        }
                        all_positions.append(normalized_pos)

                except Exception as e:
                    logger.error(f"Failed V4 scan on {network}: {e}")

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
            
            t0 = pool_data.get("token0", {})
            t1 = pool_data.get("token1", {})
            token0_symbol = t0.get("symbol", "UNKNOWN")
            token1_symbol = t1.get("symbol", "UNKNOWN")
            current_tick = int(pool_data.get("tick", 0))
            fee_tier = pool_data.get("feeTier")
            pool_tvl = float(pool_data.get("totalValueLockedUSD", 0))
            pool_vol = float(pool_data.get("volumeUSD", 0))
            
            # Sort events by timestamp ASC to aggregate correctly
            mods = pool_data.get("modifyLiquiditys", [])
            mods.sort(key=lambda x: int(x.get('timestamp', 0)))
            
            pos_aggregator = {} # token_id -> data
            
            for mod in mods:
                transfers = mod.get("transaction", {}).get("transfers", [])
                token_id = None
                owner = mod.get("origin") or mod.get("sender")
                
                if transfers:
                    token_id = transfers[0].get("tokenId")
                    owner = transfers[-1].get("to") or owner

                if not token_id:
                    token_id = f"mod-{mod.get('transaction', {}).get('id')[:10]}"

                amount = int(mod.get("amount", 0))
                
                if token_id not in pos_aggregator:
                    pos_aggregator[token_id] = {
                        "liquidity": 0,
                        "owner": owner,
                        "tick_lower": int(mod.get("tickLower", 0)),
                        "tick_upper": int(mod.get("tickUpper", 0))
                    }
                
                pos_aggregator[token_id]["liquidity"] += amount

            for token_id, agg_data in pos_aggregator.items():
                # Clamp to 0 and ignore inactive positions
                if agg_data["liquidity"] <= 0: continue
                
                normalized_pos = {
                    "protocol": "Uniswap V4",
                    "network": network,
                    "address": agg_data["owner"].lower(),
                    "position_label": f"{token0_symbol} / {token1_symbol} (Token ID: {token_id})",
                    "pool_address": pool_address.lower(),
                    "fee_tier": fee_tier,
                    "tick_lower": agg_data["tick_lower"],
                    "tick_upper": agg_data["tick_upper"],
                    "current_tick": current_tick,
                    "liquidity": str(agg_data["liquidity"]),
                    "pool_tvl": pool_tvl,
                    "pool_vol": pool_vol,
                    "assets": [
                        {"symbol": token0_symbol, "address": t0.get("id"), "decimals": int(t0.get("decimals", 18)), "balance": 0},
                        {"symbol": token1_symbol, "address": t1.get("id"), "decimals": int(t1.get("decimals", 18)), "balance": 0}
                    ],
                    "position_key": f"uniswapv4-{network}-{token_id}",
                    "balance_usd": 0
                }
                all_positions.append(normalized_pos)
                
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
