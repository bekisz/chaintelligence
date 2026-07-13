import requests
import json

GRAPH_API_KEY = "a09146d9b04d58e07e68bbdca38aa54e"
v4_subgraph_id = "G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"
url = f"https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{v4_subgraph_id}"

# Try to query the pool by ID
query = """
{
  pool(id: "0xfba5840c0593d4a63415d9c9c247f523b0f7d2e9f31b73d2f0b593d4333b1adb") {
    id
    token0 { symbol id }
    token1 { symbol id }
    feeTier
    tickSpacing
    hooks
    liquidity
  }
}
"""

resp = requests.post(url, json={"query": query}, timeout=20)
print(f"Status: {resp.status_code}")
print(json.dumps(resp.json(), indent=2))
