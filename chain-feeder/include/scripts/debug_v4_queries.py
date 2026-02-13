
import requests
import os
import json

GRAPH_API_KEY = os.getenv("GRAPH_API_KEY")
# Arbitrum V4
SUBGRAPH_URL = f"https://gateway.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"
TOKEN_ID = "103718"

def test_query(name, query):
    print(f"--- Testing {name} ---")
    try:
        resp = requests.post(SUBGRAPH_URL, json={"query": query}, timeout=10)
        data = resp.json()
        if "errors" in data:
            print("Errors:", json.dumps(data['errors'][:5], indent=2))
        else:
            print("Success!")
            # Print simplified structure
            print(json.dumps(data, indent=2)[:800] + "...") 
    except Exception as e:
        print(f"Failed: {e}")

# Try filtering by position (ID)
q1 = f"""
{{
  modifyLiquidities(first: 20, where: {{ position: "{TOKEN_ID}" }}, orderBy: timestamp, orderDirection: desc) {{
    id
    timestamp
    amount0
    amount1
    transaction {{
      id
    }}
  }}
}}
"""

# Try filtering by owner (maybe we can't do this easily without owner address, but check fields)
q2 = f"""
{{
  modifyLiquidities(first: 1) {{ # Just get one to see fields
    id
    owner
    pool {{ id }}
    position {{ id }}
    amount0
    amount1
  }}
}}
"""

if __name__ == "__main__":
    test_query("Filter by Position ID", q1)
    test_query("Inspect Fields", q2)
