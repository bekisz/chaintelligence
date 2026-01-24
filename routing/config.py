# Uniswap V3 Routing Configuration

from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()


# Token addresses on Ethereum mainnet
TOKENS = {
    'AAVE': {
        'address': '0x7fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9',
        'symbol': 'AAVE',
        'decimals': 18
    },
    'LINK': {
        'address': '0x514910771AF9CA656af84075aa92A706CE62ac07',
        'symbol': 'LINK',
        'decimals': 18
    },
    'UNI': {
        'address': '0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984',
        'symbol': 'UNI',
        'decimals': 18
    },
    'PAXG': {
        'address': '0x45804880de22913dafe09f4980848ece6ecbaf78',
        'symbol': 'PAXG',
        'decimals': 18
    },
    'USDC': {
        'address': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
        'symbol': 'USDC',
        'decimals': 6
    },
    'USDT': {
        'address': '0xdAC17F958D2ee523a2206206994597C13D831ec7',
        'symbol': 'USDT',
        'decimals': 6
    },
    'WETH': {
        'address': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
        'symbol': 'WETH',
        'decimals': 18
    },
    'EURC': {
        'address': '0x1abaea1f7c830bd89acc67ec4af516284b1bc33c',
        'symbol': 'EURC',
        'decimals': 6
    },
    'EURCV': {
        'address': '0x5F7827FDeb7c20b443265Fc2F40845B715385Ff2', 
        'symbol': 'EURCV',
        'decimals': 18
    },
    'WBTC': {
        'address': '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599',
        'symbol': 'WBTC',
        'decimals': 8
    }
}

import os

# Uniswap V3 Subgraph endpoint (The Graph Decentralized Network)
# Get API key from environment variable (optional)
GRAPH_API_KEY = os.getenv('GRAPH_API_KEY', '')

if GRAPH_API_KEY:
    # Use authenticated endpoint with higher rate limits (100k queries/month free)
    UNISWAP_V3_SUBGRAPH_URL = f'https://gateway.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV'
else:
    # Use public endpoint (limited rate, may not work without API key)
    # To get a free API key (100k queries/month), visit: https://thegraph.com/studio/
    UNISWAP_V3_SUBGRAPH_URL = 'https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV'
    print("Warning: No GRAPH_API_KEY found. Please set it in .env file or environment variable.")

# Query settings
MAX_RESULTS_PER_QUERY = 1000  # The Graph pagination limit
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3

# Get token addresses as a list for filtering
TOKEN_ADDRESSES = [token['address'].lower() for token in TOKENS.values()]

# Create reverse lookup: address -> symbol
ADDRESS_TO_SYMBOL = {token['address'].lower(): symbol for symbol, token in TOKENS.items()}
