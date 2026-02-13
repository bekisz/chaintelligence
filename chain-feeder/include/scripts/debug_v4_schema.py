
import requests
import os
import json

# Arbitrum V4 Subgraph (since ID 103718 is likely there)
# Using the same URL from backfill script
GRAPH_API_KEY = os.getenv("GRAPH_API_KEY", "f4bbb084942bd73ae157159441b69afe")
SUBGRAPH_URL = f"https://gateway.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"

TOKEN_ID = "103718" # The one with low values

def test_query(name, query):
    print(f"--- Testing {name} ---")
    try:
        resp = requests.post(SUBGRAPH_URL, json={"query": query}, timeout=10)
        data = resp.json()
        if "errors" in data:
            print("Errors:", json.dumps(data['errors'], indent=2))
        else:
            print("Success!")
            # Print simplified structure
            print(json.dumps(data, indent=2)[:500] + "...") 
    except Exception as e:
        print(f"Failed: {e}")

# 1. Try position { modifyLiquiditys }
q1 = f"""
{{
  position(id: "{TOKEN_ID}") {{
    id
    modifyLiquiditys(first: 10) {{
      id
      amount0
      amount1
      transaction {{
        id
        timestamp
      }}
    }}
  }}
}}
"""

# 2. Try top-level modifyLiquiditys with where
# Note: field name might be different? modifyLiquidities? 
q2 = f"""
{{
  modifyLiquiditys(first: 10, where: {{ position: "{TOKEN_ID}" }}) {{
    id
    amount0
    amount1
    transaction {{
        id
        timestamp
    }}
  }}
}}
"""

# 3. Try position { pool { id } } just to verify position exists
q3 = f"""
{{
  position(id: "{TOKEN_ID}") {{
    id
    pool {{
      id
    }}
  }}
}}
"""

if __name__ == "__main__":
    test_query("Position Field", q1)
    test_query("Top Level Query", q2)
    test_query("Position Existence", q3)
