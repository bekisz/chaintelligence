import requests
import os
import json
from dotenv import load_dotenv

load_dotenv('/Users/szabi/git/chaintelligence/.env.secrets')

API = os.getenv("GRAPH_API_KEY")
URL = f"https://gateway-arbitrum.network.thegraph.com/api/{API}/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"

t0, t1 = sorted([usdc, weth])

q = f"""
{{
  pools(where: {{ token0: "{t0}", token1: "{t1}", feeTier: 500 }}) {{
    id
    feeTier
    totalValueLockedUSD
    volumeUSD
    txCount
  }}
}}
"""

res = requests.post(URL, json={'query': q}).json()
print(json.dumps(res, indent=2))
