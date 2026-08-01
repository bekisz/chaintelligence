import requests
import json
from eth_hash.auto import keccak

# 1. Query Uniswap V4 Subgraph for Pool 0xd85173a34c0567501850854604460efff465b40f3121b9ad17b3bcc705e083f8
subgraph_url = "https://gateway-arbitrum.network.thegraph.com/api/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"

pool_id = "0xd85173a34c0567501850854604460efff465b40f3121b9ad17b3bcc705e083f8"

query = f"""
{{
  pool(id: "{pool_id}") {{
    id
    feeTier
    liquidity
    sqrtPrice
    tick
    totalValueLockedUSD
    totalValueLockedToken0
    totalValueLockedToken1
    token0 {{
      id
      symbol
      decimals
    }}
    token1 {{
      id
      symbol
      decimals
    }}
    hooks
  }}
}}
"""

print("=== Querying Uniswap V4 Subgraph ===")
try:
    resp = requests.post("https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v4-ethereum", json={"query": query}, timeout=10)
    print("Uniswap official subgraph status:", resp.status_code)
    print("Response:", json.dumps(resp.json(), indent=2))
except Exception as e:
    print("Official subgraph query error:", e)

# Also query Decentralized Network / public endpoints or alternative query by tokens
query_by_tokens = """
{
  pools(where: {
    token0_in: ["0x9d1a7a3191102e9f900faa10540837ba84dcbae7", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"],
    token1_in: ["0x9d1a7a3191102e9f900faa10540837ba84dcbae7", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"]
  }) {
    id
    feeTier
    totalValueLockedUSD
    totalValueLockedToken0
    totalValueLockedToken1
    token0 { symbol id }
    token1 { symbol id }
  }
}
"""

print("\n=== Searching Subgraph by Token Addresses (EURI & USDC) ===")
# Try multiple subgraph endpoints
endpoints = [
    "https://api.studio.thegraph.com/query/48241/uniswap-v4-ethereum/version/latest",
    "https://gateway.thegraph.com/api/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
]

for ep in endpoints:
    try:
        r = requests.post(ep, json={"query": query_by_tokens}, timeout=5)
        print(f"Endpoint {ep[:40]}... status {r.status_code}:")
        print(json.dumps(r.json(), indent=2))
    except Exception as e:
        print(f"Endpoint {ep[:40]}... error: {e}")
