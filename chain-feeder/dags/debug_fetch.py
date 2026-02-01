
import logging
import os
import sys
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)

load_dotenv()

# Add dags to path
sys.path.append(os.path.join(os.getcwd(), 'chain-feeder/dags'))
from uniswap_v3_range_fetcher import fetch_position_range_data

test_label = "ETH / USDC (Token ID: 111885)"
test_network = "Ethereum"
api_key = os.getenv('GRAPH_API_KEY')

print(f"Testing fetch for {test_label}...")
result = fetch_position_range_data(test_label, test_network, api_key)

if result:
    print(f"Token0: {result['token0_symbol']}")
    print(f"Token1: {result['token1_symbol']}")
    print(f"Lower: {result['price_lower']}")
    print(f"Upper: {result['price_upper']}")
    print(f"Current: {result['current_price']}")
else:
    print("Fetch failed")
