# Uniswap V3 Routing Configuration

from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()


import psycopg2

# Database Configuration (needed early for token loading)
DATA_WAREHOUSE_DB = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=localhost port=5432')

def load_tokens_from_db():
    """
    Fetch tokens from the data warehouse that have an Ethereum address.
    Fallback to a minimal set if database is unavailable.
    """
    tokens = {}
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
        cur = conn.cursor()
        # Fetch tokens with an address (which indicates they are tracked on-chain)
        # and limit to reasonable count for performance if needed, though currently 1000 is fine.
        cur.execute("""
            SELECT symbol, ethereum_address, decimals 
            FROM coin 
            WHERE ethereum_address IS NOT NULL
        """)
        rows = cur.fetchall()
        for row in rows:
            symbol, address, decimals = row
            tokens[symbol.upper()] = {
                'address': address,
                'symbol': symbol.upper(),
                'decimals': decimals if decimals is not None else 18
            }
        cur.close()
        conn.close()
        
        if not tokens:
            raise ValueError("No tokens found in database")
            
    except Exception as e:
        # Fallback minimal tokens for robustness during initial setup
        print(f"Warning: Could not load tokens from DB: {e}. using static fallback.")
        return {
            'USDC': {'address': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', 'symbol': 'USDC', 'decimals': 6},
            'USDT': {'address': '0xdAC17F958D2ee523a2206206994597C13D831ec7', 'symbol': 'USDT', 'decimals': 6},
            'WETH': {'address': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2', 'symbol': 'WETH', 'decimals': 18},
            'WBTC': {'address': '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599', 'symbol': 'WBTC', 'decimals': 8}
        }
    return tokens

# Dynamic Token Configuration
TOKENS = load_tokens_from_db()

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

# Postgres Configuration
DATA_WAREHOUSE_DB = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=localhost port=5432')
