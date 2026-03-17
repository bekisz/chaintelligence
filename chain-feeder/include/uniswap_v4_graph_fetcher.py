"""
Uniswap V4 Position Data Fetcher using The Graph
Fetches tick range data from The Graph's Uniswap V4 subgraph
"""
import requests
import logging
import math
import re

logger = logging.getLogger(__name__)

# Uniswap V4 Subgraph IDs on The Graph Decentralized Network
UNISWAP_V4_SUBGRAPH_IDS = {
    "Ethereum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",  # Placeholder - need official V4 ID
    "Arbitrum": "G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r",  # Official V4 Arbitrum
    "Base": "Gqm2b5J85n1bhCyDMpGbtbVn4935EvvdyHdHrx3dibyj",  # Official V4 Base
}

# Graph Gateway URLs (Decentralized Network) - Requires API Key
UNISWAP_V4_URLS = {
    "Ethereum": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r",
    "Base": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/Gqm2b5J85n1bhCyDMpGbtbVn4935EvvdyHdHrx3dibyj",
}

POSITION_QUERY = """
query GetPosition($tokenId: String!) {
  position(id: $tokenId) {
    id
    transfers(first: 5, orderBy: timestamp, orderDirection: desc) {
      transaction {
        id
        modifyLiquiditys(first: 5) {
          tickLower
          tickUpper
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
            feeTier
          }
        }
      }
    }
  }
}
"""


def extract_token_id(position_label):
    """
    Extract NFT token ID from position label.
    """
    if not position_label:
        return None
    
    match = re.search(r'Token ID:\s*(\d+)', position_label, re.IGNORECASE)
    if match:
        return match.group(1)
    
    match = re.search(r'#(\d+)', position_label)
    if match:
        return match.group(1)
    
    return None


def tick_to_price(tick, token0_decimals, token1_decimals):
    """
    Convert Uniswap V4 tick to human-readable price.
    """
    try:
        price = math.pow(1.0001, int(tick))
        decimal_adjustment = math.pow(10, int(token0_decimals) - int(token1_decimals))
        return price * decimal_adjustment
    except Exception as e:
        logger.error(f"Error converting tick {tick} to price: {e}")
        return 0


def fetch_v4_position_range_data_from_graph(position_label, network, graph_api_key=None):
    """
    Fetch range data for a Uniswap V4 position from The Graph.
    
    Args:
        position_label: Position label from Zapper
        network: Network name (Ethereum, Arbitrum, Base)
        graph_api_key: Graph API key for higher rate limits
    
    Returns:
        dict with range data or None if not found
    """
    # Extract token ID from label
    token_id = extract_token_id(position_label)
    if not token_id:
        logger.debug(f"No token ID found in position label: {position_label}")
        return None
    
    logger.info(f"Fetching V4 position {token_id} on {network} from The Graph")
    
    # Determine Endpoint
    if not graph_api_key:
        graph_api_key = os.getenv("GRAPH_API_KEY")

    endpoint = None
    if graph_api_key and network in UNISWAP_V4_URLS:
        endpoint = UNISWAP_V4_URLS[network].format(api_key=graph_api_key)
    
    if not endpoint:
        logger.warning(f"No valid Uniswap V4 subgraph endpoint for network: {network}")
        return None
    
    logger.info(f"Using V4 endpoint: {endpoint[:80]}...")
    
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
            logger.warning(f"V4 Position {token_id} not found on {network}")
            return None
        
        # Traverse nested structure to find ModifyLiquidity event
        # position -> transfers -> transaction -> modifyLiquiditys -> [0]
        
        found_event = None
        transfers = position.get("transfers", [])
        
        for transfer in transfers:
            transaction = transfer.get("transaction")
            if not transaction: continue
            
            # Note unusual spelling "modifyLiquiditys" from schema
            mods = transaction.get("modifyLiquiditys", [])
            if mods:
                found_event = mods[0]
                break
        
        if not found_event:
            logger.warning(f"No ModifyLiquidity events found for V4 position {token_id}")
            return None
            
        # Extract tick data with defensive checks
        try:
            # V4 subgraph returns ticks as strings directly in ModifyLiquidity
            tick_lower = int(found_event["tickLower"])
            tick_upper = int(found_event["tickUpper"])
            
            pool = found_event.get("pool")
            if not pool:
                logger.error(f"Missing pool info in ModifyLiquidity for {token_id}")
                return None

            current_tick = int(pool["tick"])
        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"Error extracting V4 tick data for {token_id}: {e}")
            return None
        
        # Get token info
        token0 = pool.get("token0")
        token1 = pool.get("token1")
        
        if not token0 or not token1:
            logger.error(f"Missing V4 token info for {token_id}")
            return None
        
        token0_decimals = int(token0.get("decimals", 18))
        token1_decimals = int(token1.get("decimals", 18))
        
        # Convert ticks to prices
        price_lower = tick_to_price(tick_lower, token0_decimals, token1_decimals)
        price_upper = tick_to_price(tick_upper, token0_decimals, token1_decimals)
        current_price = tick_to_price(current_tick, token0_decimals, token1_decimals)
        
        # Price inversion logic (same as V3)
        stablecoins = ["USDC", "USDT", "DAI", "USDBC"]
        quote_currencies = ["WETH", "ETH"]
        token0_sym = token0["symbol"].upper()
        token1_sym = token1["symbol"].upper()
        
        should_invert = False
        
        if any(s in token0_sym for s in stablecoins) and not any(s in token1_sym for s in stablecoins):
            should_invert = True
        elif any(q in token0_sym for q in quote_currencies) and \
             not any(s in token1_sym for s in stablecoins) and \
             not any(q in token1_sym for q in quote_currencies):
            should_invert = True
        
        if should_invert:
            p_l = tick_to_price(tick_lower, token0_decimals, token1_decimals)
            p_u = tick_to_price(tick_upper, token0_decimals, token1_decimals)
            p_c = tick_to_price(current_tick, token0_decimals, token1_decimals)
            
            price_lower = 1 / p_u if p_u != 0 else 0
            price_upper = 1 / p_l if p_l != 0 else 0
            current_price = 1 / p_c if p_c != 0 else 0
            
            logger.info(f"Inverting V4 price for {token0_sym}/{token1_sym}")
        
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
            "fee_tier": pool.get("feeTier", 0)
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed for V4 position {token_id} on {network}: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error fetching V4 position {token_id}: {e}")
        return None
