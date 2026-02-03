"""
Uniswap V4 RPC Fetcher
Fetches position data from Uniswap V4 using Public RPC and External Prices (DefiLlama).
"""
import requests
import logging
import math
import re
import os

logger = logging.getLogger(__name__)

# Constants
RPC_URLS = [
    "https://rpc.ankr.com/eth/2087a416f7a49024a0de38a87ae2c088cf7aaa743e57d7c9c8c9573aed7829de", # User Provided Valid Key
    "https://eth.llamarpc.com",
    "https://1rpc.io/eth", 
    "https://cloudflare-eth.com"
]
POSITION_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"

# Selectors (Verified)
SEL_GET_INFO = "0x7ba03aad" # getPoolAndPositionInfo(uint256)
SEL_DECIMALS = "0x313ce567" # decimals()
SEL_SYMBOL = "0x95d89b41" # symbol()

# Stablecoins for Inversion (Heuristic)
STABLECOIN_SYMBOLS = ["USDC", "USDT", "DAI", "USDBC", "EUROC", "EURC", "PYUSD", "USDS", "GHO", "FRAX"]
# Known Addresses (Mainnet) to save RPC calls
KNOWN_DECIMALS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6, # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6, # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f": 18, # DAI
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18, # WETH
}

def call_rpc(method, params, id=1):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": id}
    
    # Try logic: Configured RPC first, then fallbacks
    urls_to_try = []
    env_rpc = os.environ.get("RPC_URL")
    if env_rpc: urls_to_try.append(env_rpc)
    urls_to_try.extend(RPC_URLS)
    
    for url in urls_to_try:
        try:
            resp = requests.post(url, json=payload, timeout=5) # 5s timeout per attempt
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data:
                    return data["result"]
                if "error" in data:
                    logger.warning(f"RPC Error {url}: {data['error']}")
                    continue
        except Exception as e:
            logger.warning(f"RPC Fail {url}: {e}")
            continue
            
    return None

def extract_token_id(position_label):
    if not position_label: return None
    match = re.search(r'Token ID:\s*(\d+)', position_label, re.IGNORECASE)
    if match: return match.group(1)
    match = re.search(r'#(\d+)', position_label)
    if match: return match.group(1)
    return None

def decode_string_rpc(hex_res):
    try:
        raw = hex_res[2:]
        length = int(raw[64:128], 16)
        data_hex = raw[128:128 + length*2]
        return bytearray.fromhex(data_hex).decode('utf-8')
    except:
        return None


def fetch_symbol(addr):
    # Handle native ETH (zero address in V4)
    if addr.lower() == "0x0000000000000000000000000000000000000000":
        return "ETH"
    res = call_rpc("eth_call", [{"to": addr, "data": SEL_SYMBOL}, "latest"])
    if res:
        return decode_string_rpc(res)
    return "UNKNOWN"

def fetch_decimals(addr):
    # Handle native ETH (zero address in V4)
    if addr.lower() == "0x0000000000000000000000000000000000000000":
        return 18
    if addr.lower() in KNOWN_DECIMALS:
        return KNOWN_DECIMALS[addr.lower()]
    res = call_rpc("eth_call", [{"to": addr, "data": SEL_DECIMALS}, "latest"])
    if res and res != "0x":
        try:
            return int(res, 16)
        except ValueError:
            return 18
    return 18


def parse_int24(val):
    if val & 0x800000:
        return val - 0x1000000
    return val

def unpack_position_info(info_int):
    tick_upper_raw = (info_int >> 32) & 0xFFFFFF
    tick_lower_raw = (info_int >> 8) & 0xFFFFFF
    return parse_int24(tick_lower_raw), parse_int24(tick_upper_raw)

def tick_to_price(tick, d0, d1):
    try:
        price = math.pow(1.0001, int(tick))
        decimal_adjustment = math.pow(10, int(d0) - int(d1))
        return price * decimal_adjustment
    except: return 0

def fetch_token_prices(c0, c1):
    url = f"https://coins.llama.fi/prices/current/ethereum:{c0},ethereum:{c1}"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("coins", {})
            p0 = data.get(f"ethereum:{c0}", {}).get("price", 0)
            p1 = data.get(f"ethereum:{c1}", {}).get("price", 0)
            return p0, p1
        else:
            logger.warning(f"V4-LLAMA: Price fetch status {resp.status_code}")
    except Exception as e:
        logger.warning(f"V4-LLAMA: Price fetch failed: {e}")
    return 0, 0

def fetch_token_prices_from_db(s0, s1):
    """Lookup prices in the coin table using PostgresHook."""
    try:
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        
        def _get(sym):
            if not sym: return 0.0
            # Symbols are stored as normalized by zapper (usually upper or truncated)
            res = hook.get_first("SELECT price FROM coin WHERE UPPER(symbol) = %s", (sym.upper(),))
            return float(res[0]) if res and res[0] is not None else 0.0
            
        return _get(s0), _get(s1)
    except Exception as e:
        logger.warning(f"DB Price Fetch failed: {e}")
        return 0.0, 0.0

def fetch_v4_position_range_data(position_label, network, graph_api_key=None):
    if network.lower() not in ["ethereum", "mainnet"]:
        return None
        
    tid_str = extract_token_id(position_label)
    if not tid_str: return None
    tid = int(tid_str)
    
    # 1. Get Pool & Position Info
    calldata = SEL_GET_INFO + format(tid, '064x')
    res_hex = call_rpc("eth_call", [{"to": POSITION_MANAGER, "data": calldata}, "latest"])
    
    if not res_hex or res_hex == "0x":
        logger.warning(f"V4: Failed to get info for Token {tid}")
        return None
        
    raw = res_hex[2:]
    if len(raw) < 384:
        logger.error("V4: Invalid response length")
        return None
        
    words = [int(raw[i:i+64], 16) for i in range(0, len(raw), 64)]
    
    # Currency0/1 addresses (Words 0, 1)
    c0_addr = "0x" + format(words[0], '040x')
    c1_addr = "0x" + format(words[1], '040x')
    
    # Position Info (Word 5)
    pos_info = words[5]
    tick_lower, tick_upper = unpack_position_info(pos_info)
    
    # Ensure Lower <= Upper
    if tick_lower > tick_upper:
        tick_lower, tick_upper = tick_upper, tick_lower
        
    # 5. Metadata
    d0 = fetch_decimals(c0_addr)
    d1 = fetch_decimals(c1_addr)
    s0 = fetch_symbol(c0_addr)
    s1 = fetch_symbol(c1_addr)
    
    # 4. Current Tick (Uses Prices from DB)
    p0_usd, p1_usd = fetch_token_prices_from_db(s0, s1)
    current_tick = 0
    
    if p0_usd > 0 and p1_usd > 0:
         ratio = p0_usd / p1_usd
         # Raw = Ratio * 10^(d1-d0)
         raw_price = ratio * (10**(d1-d0))
         if raw_price > 0:
             current_tick = int(math.log(raw_price) / math.log(1.0001))
    else:
         logger.warning(f"V4: No external price for {c0_addr}/{c1_addr} (USDC:{p0_usd}, WETH:{p1_usd}). Fallback to Middle.")
         
    # Fallback: If current_tick is 0 (Failed fetch), use Middle of Range
    # This prevents 'Unrealistic Price' of 1.0/10^12 and ensures slider is centered.
    if current_tick == 0:
        current_tick = (tick_lower + tick_upper) // 2
    
    # 6. Prices
    p_l = tick_to_price(tick_lower, d0, d1)
    p_u = tick_to_price(tick_upper, d0, d1)
    p_c = tick_to_price(current_tick, d0, d1)
    
    # Invert Logic
    s0 = s0 or "UNKNOWN"
    s1 = s1 or "UNKNOWN"
    
    # Determine if we should invert the price display
    # We want to show prices as: BaseToken per QuoteToken
    # Quote tokens are typically: stablecoins (USDC, USDT, DAI) or major currencies (ETH, WETH)
    # If token0 is a quote currency and token1 is not, we should invert
    
    QUOTE_CURRENCIES = ["WETH", "ETH"]  # Major quote currencies besides stablecoins
    
    should_invert = False
    
    # Check if token0 is a stablecoin and token1 is not
    if any(s in s0.upper() for s in STABLECOIN_SYMBOLS) and not any(s in s1.upper() for s in STABLECOIN_SYMBOLS):
         should_invert = True
    
    # Check if token0 is ETH/WETH and token1 is not a stablecoin or ETH/WETH
    # This handles cases like ETH-UNI where we want to show UNI/ETH price
    elif any(q in s0.upper() for q in QUOTE_CURRENCIES) and \
         not any(s in s1.upper() for s in STABLECOIN_SYMBOLS) and \
         not any(q in s1.upper() for q in QUOTE_CURRENCIES):
         should_invert = True
         
    if should_invert:
         price_lower = 1 / p_u if p_u != 0 else 0
         price_upper = 1 / p_l if p_l != 0 else 0
         current_price = 1 / p_c if p_c != 0 else 0
    else:
         price_lower = p_l
         price_upper = p_u
         current_price = p_c
         
    in_range = tick_lower <= current_tick <= tick_upper
    
    return {
        "tick_lower": tick_lower,
        "tick_upper": tick_upper,
        "current_tick": current_tick,
        "price_lower": price_lower,
        "price_upper": price_upper,
        "current_price": current_price,
        "in_range": in_range,
        "fee_tier": 0
    }
