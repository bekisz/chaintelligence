
import requests
import os
import json

GRAPH_API_KEY = os.getenv("GRAPH_API_KEY")
# Using Arbitrum endpoint as it seems to be the most active/official V4 deployment according to file comments
# But user position is on Ethereum? Let's try Ethereum endpoint first, even if placeholder.
# If Ethereum fails, try Arbitrum.
# The user's position ID 125031 might be on Arbitrum or Base if the DB network is just a label?
# Or maybe the Ethereum endpoint is valid.

# From uniswap_v4_graph_fetcher.py
UNISWAP_V4_URLS = {
    "Ethereum": "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r",
}

POSITION_QUERY = """
query GetPosition($tokenId: String!) {
  position(id: $tokenId) {
    id
    transfers(first: 5, orderBy: timestamp, orderDirection: desc) {
      transaction {
        id
        timestamp
        modifyLiquiditys(first: 5) {
          amount0
          amount1
          tickLower
          tickUpper
          pool {
            tick
          }
        }
      }
    }
  }
}
"""

def test_query(network, token_id):
    endpoint = UNISWAP_V4_URLS.get(network).format(api_key=GRAPH_API_KEY)
    print(f"Testing {network} with Token {token_id} at {endpoint[:60]}...")
    
    try:
        resp = requests.post(endpoint, json={"query": POSITION_QUERY, "variables": {"tokenId": str(token_id)}}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "errors" in data:
                print(f"Errors: {data['errors']}")
            else:
                print(f"Success! Data: {json.dumps(data, indent=2)}")
        else:
            print(f"Status {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Request Error: {e}")

if __name__ == "__main__":
    # Test Ethereum with Token 125031
    # test_query("Ethereum", "125031")
    # Also test Arbitrum just in case
    test_query("Arbitrum", "125031")
