"""
Uniswap V3 Position Data Fetcher
Fetches tick range data from The Graph's Uniswap V3 subgraph
"""
import requests
import logging
import math
import re

logger = logging.getLogger(__name__)

# Uniswap V3 Subgraph IDs on The Graph Network
UNISWAP_V3_SUBGRAPH_IDS = {
    "Ethereum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV", # Same ID often works on L2s via different endpoint, but checking... actually Arbitrum V3 is different.
    # Canonical Arbitrum V3 on Graph Network: F7q3... NO, let's stick to known working ones or fallback.
    # Using the IDs found in the JS file: Arbitrum V3 IDs were empty in JS!
    # Let's use the explicit Decentralized ID for Ethereum, and keep hosted service as fallback for others if needed, but Gateway is best.
    
    # Valid Subgraph IDs (Decentralized Network)
    "Ethereum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "H6Kuwy22UgyXQC4y89Gz28d11aQeUv6Qy7G3G6d3XW2w", # Example Arbitrum ID
    "Polygon": "H6Kuwy22UgyXQC4y89Gz28d11aQeUv6Qy7G3G6d3XW2w", # Placeholder if needed
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
    tickLower {
      tickIdx
    }
    tickUpper {
      tickIdx
    }
    pool {
      tick
      token0 {
        symbol
        decimals
      }
      token1 {
        symbol
        decimals
      }
      token0Price
      token1Price
      feeTier
    }
  }
}
"""


def extract_token_id(position_label):
    """
    Extract NFT token ID from position label.
    Examples:
    - "ETH / USDC (Token ID: 103718)" -> "103718"
    - "WETH - USDC #12345" -> "12345"
    """
    if not position_label:
        return None
    
    # Try "Token ID: XXXXX" pattern
    match = re.search(r'Token ID:\s*(\d+)', position_label, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # Try "#XXXXX" pattern
    match = re.search(r'#(\d+)', position_label)
    if match:
        return match.group(1)
    
    return None


def tick_to_price(tick, token0_decimals, token1_decimals):
    """
    Convert Uniswap V3 tick to human-readable price.
    Formula: price = 1.0001^tick * (10^(token0_decimals - token1_decimals))
    """
    try:
        price = math.pow(1.0001, int(tick))
        decimal_adjustment = math.pow(10, int(token0_decimals) - int(token1_decimals))
        return price * decimal_adjustment
    except Exception as e:
        logger.error(f"Error converting tick {tick} to price: {e}")
        return 0


def fetch_position_range_data(position_label, network, graph_api_key=None):
    """
    Fetch range data for a Uniswap V3 position from The Graph.
    
    Args:
        position_label: Position label from Zapper (e.g., "ETH / USDC (Token ID: 103718)")
        network: Network name (Ethereum, Arbitrum, Base, Polygon)
        graph_api_key: Optional Graph API key for higher rate limits
    
    Returns:
        dict with range data or None if not found/applicable
        {
            "token_id": "103718",
            "tick_lower": -887220,
            "tick_upper": 887220,
            "current_tick": 204567,
            "price_lower": 1800.50,
            "price_upper": 2200.75,
            "current_price": 2000.25,
            "in_range": True,
            "token0_symbol": "WETH",
            "token1_symbol": "USDC",
            "fee_tier": "3000"
        }
    """
    # Extract token ID from label
    token_id = extract_token_id(position_label)
    if not token_id:
        logger.debug(f"No token ID found in position label: {position_label}")
        return None
    
    # Determine Endpoint
    endpoint = None
    if graph_api_key and network in UNISWAP_V3_URLS:
        # Inject API key into URL template
        endpoint = UNISWAP_V3_URLS[network].format(api_key=graph_api_key)
    elif network in UNISWAP_V3_URLS:
         # Fallback for non-template URLs
         url = UNISWAP_V3_URLS[network]
         if "{api_key}" not in url:
             endpoint = url
    
    if not endpoint:
        logger.warning(f"No valid Uniswap V3 subgraph endpoint for network: {network}")
        return None
    
    # Headers
    headers = {"Content-Type": "application/json"}
    
    try:
        # Query The Graph
        response = requests.post(
            endpoint,
            json={"query": POSITION_QUERY, "variables": {"tokenId": token_id}},
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if "errors" in data:
            logger.error(f"GraphQL errors for token {token_id} on {endpoint}: {data['errors']}")
            return None
        
        position = data.get("data", {}).get("position")
        if not position:
            logger.warning(f"Position {token_id} not found on {network}")
            return None
        
        # Extract tick data
        tick_lower = int(position["tickLower"]["tickIdx"])
        tick_upper = int(position["tickUpper"]["tickIdx"])
        current_tick = int(position["pool"]["tick"])
        
        # Get token info
        token0 = position["pool"]["token0"]
        token1 = position["pool"]["token1"]
        token0_decimals = int(token0["decimals"])
        token1_decimals = int(token1["decimals"])
        
        # Convert ticks to prices
        price_lower = tick_to_price(tick_lower, token0_decimals, token1_decimals)
        price_upper = tick_to_price(tick_upper, token0_decimals, token1_decimals)
        current_price = tick_to_price(current_tick, token0_decimals, token1_decimals)
        
        # Check for price inversion needed (if token0 is stablecoin/quote)
        # We generally want Base/Quote price. 
        # If Token0 is USDC/USDT, then standard price is Token1 per Token0 (very small number).
        # We want Token0 per Token1 (Inverse).
        # Common stablecoins: USDC, USDT, DAI, USDbC
        stablecoins = ["USDC", "USDT", "DAI", "USDBC"]
        token0_sym = token0["symbol"].upper()
        token1_sym = token1["symbol"].upper()
        
        should_invert = False
        if any(s in token0_sym for s in stablecoins) and not any(s in token1_sym for s in stablecoins):
             should_invert = True
        
        if should_invert:
            # Avoid division by zero
            price_lower = 1 / price_upper if price_upper != 0 else 0
            price_upper = 1 / price_lower if price_lower != 0 else 0 # Actually price_lower was the old lower tick, which corresponds to lower price? No.
            # Tick logic: Lower tick = lower price IS ONLY TRUE if not inverted.
            # If inverted: 
            # Price = 1.0001^tick.
            # Inverted Price = 1 / 1.0001^tick = 1.0001^(-tick).
            # Lower tick index -> Lower price in standard terms.
            # If we invert: P_inv = 1/P.
            # If P_lower < P_upper. Then 1/P_lower > 1/P_upper.
            # So New Lower = 1 / Old Upper. New Upper = 1 / Old Lower.
            
            # Recalculate correctly
            p_l = tick_to_price(tick_lower, token0_decimals, token1_decimals)
            p_u = tick_to_price(tick_upper, token0_decimals, token1_decimals)
            p_c = tick_to_price(current_tick, token0_decimals, token1_decimals)
            
            price_lower = 1 / p_u if p_u != 0 else 0
            price_upper = 1 / p_l if p_l != 0 else 0
            current_price = 1 / p_c if p_c != 0 else 0
            
            logger.info(f"Inverting price for {token0_sym}/{token1_sym} (Stable as Token0)")

        # Validate Symbols against Label
        # If label is "ETH / USDC" and we fetched "OXT" / "WETH", it's a mismatch (Token ID collision).
        def check_symbols_match(label, t0_sym, t1_sym):
            label_upper = label.upper()
            t0 = t0_sym.upper()
            t1 = t1_sym.upper()
            
            # Helper to check if S is in Label, handling W-tokens (WETH->ETH)
            def is_in_label(sym, lbl):
                if sym in lbl: return True
                if sym.startswith('W') and len(sym) > 3 and sym[1:] in lbl: return True # WETH->ETH, WBTC->BTC
                if sym == "USDBC" and "USDC" in lbl: return True # Base USDbC usually labeled USDC
                if sym == "EUROC" and "EURC" in lbl: return True # Circle Euro Renamed
                return False

            # We expect BOTH tokens to be roughly present in the label
            # But sometimes labels are "Pool Name" which might not have tickers.
            # Zapper labels: "ETH / USDC ...", "WETH - USDC"
            # If neither token is in label, it's definitely suspicious.
            # If one is missing, it might be partial.
            # Let's be strict: At least one MUST match perfectly, and the other should plausible.
            
            match0 = is_in_label(t0, label_upper)
            match1 = is_in_label(t1, label_upper)
            
            if not match0 and not match1:
                return False
                
            # If we have "OXT" and "WETH" and label is "ETH / USDC". 
            # WETH matches ETH. OXT does not match USDC.
            # So match1 is True, match0 is False.
            # This is ambiguous. But OXT is definitely not USDC.
            # If one matches, we assume it *might* be correct unless the other is totally alien?
            # Better heuristic: parsed label tokens?
            # Let's split label by " / ", "-", " "
            label_parts = re.split(r'[\s\/\-\(\)]+', label_upper)
            
            # Check if t0 or t1 are present in parts
            # But strict check: If fetched says "OXT", and label doesn't have "OXT", it's suspicious?
            # No, label might be "Orchid / WETH".
            
            # Let's rely on the user's specific case: "ETH / USDC" vs "OXT / WETH".
            # OXT is not in label. USDC is in label but not in fetched.
            # If a major token (USDC, ETH, WBTC) is in Label but NOT in fetched, it's bad.
            
            major_tokens = ["ETH", "WETH", "USDC", "USDT", "DAI", "WBTC"]
            for maj in major_tokens:
                 if maj in label_parts and maj not in [t0, t1, t0[1:] if t0.startswith('W') else t0]:
                      # Major token in label missing from fetched?
                      # ETH is in label "ETH / USDC".
                      # Fetched: OXT, WETH.
                      # matches: WETH->ETH.
                      # USDC is in label. Fetched doesn't have USDC.
                      # So we flag it?
                      pass
            
            # Proposed Logic:
            # If the label contains explicit tickers "X / Y", then Fetched must contain X (or WX) and Y (or WY).
            # If Fetched contains symbol S (e.g. OXT) that is NOT in label, and Label seems to be complete tickers (has / or -), reject.
            
            return match0 and match1 # Strict: Both must be identifiable in label
            
        if not check_symbols_match(position_label, token0["symbol"], token1["symbol"]):
            logger.warning(f"Symbol mismatch for {token_id}. Label: {position_label}, Fetched: {token0['symbol']}/{token1['symbol']}")
            return None
        
        # Determine if in range
        in_range = tick_lower <= current_tick <= tick_upper

        return {
            "token_id": token_id,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "current_tick": current_tick,
            "price_lower": price_lower,
            "price_upper": price_upper,
            "current_price": current_price,
            "in_range": in_range,
            "token0_symbol": token0["symbol"],
            "token1_symbol": token1["symbol"],
            "fee_tier": position["pool"]["feeTier"]
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed for position {token_id} on {network}: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error fetching position {token_id}: {e}")
        return None


if __name__ == "__main__":
    # Test with a sample position
    logging.basicConfig(level=logging.INFO)
    
    test_label = "ETH / USDC (Token ID: 103718)"
    test_network = "Ethereum"
    
    result = fetch_position_range_data(test_label, test_network)
    if result:
        print(f"Position {result['token_id']}:")
        print(f"  Range: {result['price_lower']:.2f} - {result['price_upper']:.2f}")
        print(f"  Current: {result['current_price']:.2f}")
        print(f"  In Range: {result['in_range']}")
    else:
        print("No range data found")
