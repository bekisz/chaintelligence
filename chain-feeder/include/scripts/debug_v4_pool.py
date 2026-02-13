import requests
import json
import os
import sys

# Add parent dir to path to import fetcher if needed, or just copy constants
sys.path.append(os.path.join(os.path.dirname(__file__), '../'))

GRAPH_API_KEY = os.getenv("GRAPH_API_KEY", "f4bbb084942bd73ae157159441b69afe") # From .env
SUBGRAPH_ID = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
ENDPOINT = f"https://gateway.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{SUBGRAPH_ID}"

# Use a known Token ID. From DB query earlier: 
# 103718, 111885, 111886. 
# Screenshot had ...c425. Let's try to search by position ID if possible?
# But query uses Token ID. 
# Let's use 111885 (returned in DB query for ETH-USDC V4).
TOKEN_ID = "111885" 

POSITION_QUERY = """
query GetPosition($tokenId: String!) {
  position(id: $tokenId) {
    id
    transfers(first: 1, orderBy: timestamp, orderDirection: desc) {
      transaction {
        modifyLiquiditys(first: 1) {
          tickLower
          tickUpper
          pool {
            id
            tick
            token0 { symbol }
            token1 { symbol }
          }
        }
      }
    }
  }
}
"""

POOL_QUERY = """
query GetPool($poolId: ID!) {
  pool(id: $poolId) {
    id
    tick
    token0 { symbol }
    token1 { symbol }
  }
}
"""

def run():
    print(f"Querying V4 Subgraph (Ethereum): {ENDPOINT}")
    
    # 1. Fetch Position and Nested Pool
    headers = {"Content-Type": "application/json"}
    resp = requests.post(ENDPOINT, json={"query": POSITION_QUERY, "variables": {"tokenId": TOKEN_ID}}, headers=headers)
    
    if resp.status_code != 200:
        print(f"Error fetching position: {resp.text}")
        return

    print(f"Response: {resp.text}")
    data = resp.json()
    pos = data.get("data", {}).get("position")
    
    if not pos:
        print("Position not found")
        return

    print("--- Nested Query Result ---")
    transfers = pos.get("transfers", [])
    if not transfers:
        print("No transfers found")
        return

    mods = transfers[0]["transaction"]["modifyLiquiditys"]
    if not mods:
        print("No ModifyLiquidity found")
        return

    nested_pool = mods[0]["pool"]
    print(f"Pool ID: {nested_pool['id']}")
    print(f"Pool Tick (Nested): {nested_pool['tick']}")
    print(f"Token0: {nested_pool['token0']['symbol']}")
    print(f"Token1: {nested_pool['token1']['symbol']}")
    
    pool_id = nested_pool['id']
    
    # 2. Fetch Pool Directly
    print("\n--- Direct Pool Query Result ---")
    resp_pool = requests.post(ENDPOINT, json={"query": POOL_QUERY, "variables": {"poolId": pool_id}}, headers=headers)
    
    if resp_pool.status_code != 200:
        print(f"Error fetching pool: {resp_pool.text}")
        return

    pool_data = resp_pool.json().get("data", {}).get("pool")
    if not pool_data:
        print("Pool not found directly")
        return

    print(f"Pool Tick (Direct): {pool_data['tick']}")
    
    if int(nested_pool['tick']) != int(pool_data['tick']):
        print("\nMISMATCH DETECTED! Nested query returns stale data.")
    else:
        print("\nValues match. Subgraph might be stale/not updating.")

if __name__ == "__main__":
    run()
