
import requests
import os
import json

GRAPH_API_KEY = os.getenv("GRAPH_API_KEY", "f4bbb084942bd73ae157159441b69afe")
SUBGRAPH_URL = f"https://gateway.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/Gqm2b5J85n1bhCyDMpGbtbVn4935EvvdyHdHrx3dibyj" # Base V4


TOKEN_ID = "103718"
# I need to fetch the position details first to get the filters
def get_position_details():
    query = f"""
    {{
      position(id: "{TOKEN_ID}") {{
        pool {{ id }}
        owner
        # On V4 subgraph, owner might be the Abstract user or just address
        # We also need ticks.
        # Position entity in this subgraph has `tokenId` but does it have ticks?
        # Introspection said: id, tokenId, owner, origin, createdAtTimestamp... transfers.
        # It DOES NOT have ticks!
        
        # So I have to get Ticks from the "Mint" transfer (Log 0 -> ModifyLiquidity)?
        # Or look at the first ModifyLiquidity linked to the transfer?
        
        transfers(first: 1) {{
          transaction {{
             modifyLiquidities(first: 1) {{
               pool {{ id }}
               tickLower
               tickUpper
               origin
             }}
          }}
        }}
      }}
    }}
    """
    resp = requests.post(SUBGRAPH_URL, json={"query": query}, timeout=10)
    data = resp.json()
    if 'data' in data and data['data']['position']:
        pos = data['data']['position']
        # Extract details from the first transfer/modification
        if pos['transfers'] and pos['transfers'][0]['transaction']['modifyLiquidities']:
            mod = pos['transfers'][0]['transaction']['modifyLiquidities'][0]
            return {
                "pool_id": mod['pool']['id'],
                "tick_lower": mod['tickLower'],
                "tick_upper": mod['tickUpper'],
                "origin": mod['origin'] # User address usually
            }
    return None

def fetch_all_modifications(filters):
    print("Filters:", filters)
    query = f"""
    {{
      modifyLiquidities(where: {{ 
        pool: "{filters['pool_id']}",
        origin: "{filters['origin']}",
        tickLower: {filters['tick_lower']},
        tickUpper: {filters['tick_upper']}
      }}, orderBy: timestamp, orderDirection: asc) {{
        id
        timestamp
        amount0
        amount1
        transaction {{ id }}
      }}
    }}
    """
    resp = requests.post(SUBGRAPH_URL, json={"query": query}, timeout=10)
    data = resp.json()
    if 'data' in data:
        return data['data']['modifyLiquidities']
    else:
        print("Error fetching mods:", data)
        return []

if __name__ == "__main__":
    print("Fetching position details...")
    filters = get_position_details()
    if filters:
        print("Fetching all modifications...")
        mods = fetch_all_modifications(filters)
        print(f"Found {len(mods)} events.")
        for m in mods:
            print(f"{m['timestamp']} | {m['amount0']} / {m['amount1']} | {m['transaction']['id']}")
    else:
        print("Could not find position details.")
