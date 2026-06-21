import requests
import os
import json
from dotenv import load_dotenv

load_dotenv('/Users/szabi/git/chaintelligence/.env.secrets')

API = os.getenv("GRAPH_API_KEY")
URL = f"https://gateway-arbitrum.network.thegraph.com/api/{API}/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"

# Token ordering: token0 < token1
t0, t1 = sorted([usdc, weth])

print(f"Querying for token0={t0}, token1={t1}")

# Try query with feeTier as string
q1 = f"""
{{
  pools(where: {{ token0: "{t0}", token1: "{t1}" }}) {{
    id
    feeTier
    liquidity
    totalValueLockedUSD
  }}
}}
"""

res = requests.post(URL, json={'query': q1}).json()
print("All USDC-WETH pools:")
print(json.dumps(res, indent=2))
