import logging
import os
import re
import math
import requests

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Uniswap V3 Subgraph IDs on The Graph Network
UNISWAP_V3_SUBGRAPH_IDS = {
    "Ethereum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "H6Kuwy22UgyXQC4y89Gz28d11aQeUv6Qy7G3G6d3XW2w",
    "Polygon": "H6Kuwy22UgyXQC4y89Gz28d11aQeUv6Qy7G3G6d3XW2w",
}

# Fallback/Legacy URLs
UNISWAP_V3_URLS = {
    "Ethereum": "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-arbitrum",
    "Base": "https://api.studio.thegraph.com/query/48211/uniswap-v3-base/version/latest",
    "Polygon": "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon",
}

POSITION_QUERY = """
query GetPosition($tokenId: String!) {
  position(id: $tokenId) {
    id
    tickLower { tickIdx }
    tickUpper { tickIdx }
    pool {
      tick
      token0 { symbol decimals }
      token1 { symbol decimals }
      token0Price
      token1Price
      feeTier
    }
  }
}
"""

def extract_token_id(position_label):
    if not position_label: return None
    match = re.search(r'Token ID:\s*(\d+)', position_label, re.IGNORECASE)
    if match: return match.group(1)
    match = re.search(r'#(\d+)', position_label)
    if match: return match.group(1)
    return None

def tick_to_price(tick, token0_decimals, token1_decimals):
    try:
        price = math.pow(1.0001, int(tick))
        decimal_adjustment = math.pow(10, int(token0_decimals) - int(token1_decimals))
        return price * decimal_adjustment
    except Exception as e:
        logger.error(f"Error converting tick {tick}: {e}")
        return 0

def fetch_position_range_data(position_label, network, graph_api_key=None):
    token_id = extract_token_id(position_label)
    if not token_id:
        return None
    
    endpoint = None
    if graph_api_key and network in UNISWAP_V3_URLS:
        endpoint = UNISWAP_V3_URLS[network].format(api_key=graph_api_key)
    elif network in UNISWAP_V3_URLS:
         url = UNISWAP_V3_URLS[network]
         if "{api_key}" not in url:
             endpoint = url
    
    if not endpoint:
        logger.warning(f"No valid endpoint for {network}")
        return None
    
    try:
        response = requests.post(endpoint, json={"query": POSITION_QUERY, "variables": {"tokenId": token_id}}, headers={"Content-Type": "application/json"}, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        position = data.get("data", {}).get("position")
        if not position:
            logger.warning(f"Position {token_id} not found.")
            return None
        
        tick_lower = int(position["tickLower"]["tickIdx"])
        tick_upper = int(position["tickUpper"]["tickIdx"])
        current_tick = int(position["pool"]["tick"])
        
        token0 = position["pool"]["token0"]
        token1 = position["pool"]["token1"]
        token0_decimals = int(token0["decimals"])
        token1_decimals = int(token1["decimals"])
        
        price_lower = tick_to_price(tick_lower, token0_decimals, token1_decimals)
        price_upper = tick_to_price(tick_upper, token0_decimals, token1_decimals)
        current_price = tick_to_price(current_tick, token0_decimals, token1_decimals)
        
        # Inversion logic for Stablecoins as Token0 (e.g. USDC/WETH -> WETH/USDC)
        stablecoins = ["USDC", "USDT", "DAI", "USDBC"]
        token0_sym = token0["symbol"].upper()
        token1_sym = token1["symbol"].upper()
        
        should_invert = False
        if any(s in token0_sym for s in stablecoins) and not any(s in token1_sym for s in stablecoins):
             should_invert = True
             
        if should_invert:
            logger.info(f"Inverting price for {token0_sym}/{token1_sym}")
            # Recalc from ticks
            p_l = tick_to_price(tick_lower, token0_decimals, token1_decimals)
            p_u = tick_to_price(tick_upper, token0_decimals, token1_decimals)
            p_c = tick_to_price(current_tick, token0_decimals, token1_decimals)
            
            # 1 / P
            # Swap bounds: Lower P becomes Higher 1/P?
            # If Tick L corresponds to Price L.
            # If Price L < Price U.
            # 1/Price L > 1/Price U.
            # So New Lower Bound = 1/Price U. New Upper Bound = 1/Price L.
            
            price_lower = 1 / p_u if p_u != 0 else 0
            price_upper = 1 / p_l if p_l != 0 else 0
            current_price = 1 / p_c if p_c != 0 else 0
            
        in_range = tick_lower <= current_tick <= tick_upper
        
        # Validation
        # Rough check if fetched symbols match label
        if "ETH" in position_label.upper() and ("ETH" not in token0_sym and "ETH" not in token1_sym and "WETH" not in token0_sym and "WETH" not in token1_sym):
             logger.warning(f"Mismatch: Label {position_label} vs Fetched {token0_sym}/{token1_sym}")
             # return None # Strict check
        
        return {
            "token_id": token_id,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "current_tick": current_tick,
            "price_lower": price_lower,
            "price_upper": price_upper,
            "current_price": current_price,
            "in_range": in_range
        }
    except Exception as e:
        logger.error(f"Error: {e}")
        return None

if __name__ == "__main__":
    api_key = os.getenv("GRAPH_API_KEY")
    print(f"DEBUG: Using API KEY: {api_key if api_key else 'None'}")
    
    test_cases = [
        ("ETH / USDC (Token ID: 103718)", "Ethereum"),
        ("EURC - EURCV #124668", "Ethereum"),
    ]
    
    for label, network in test_cases:
        print(f"\n--- Testing {label} ---")
        res = fetch_position_range_data(label, network, api_key)
        print(res)
