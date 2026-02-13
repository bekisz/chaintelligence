
import requests
import os
import json

GRAPH_API_KEY = os.getenv("GRAPH_API_KEY", "f4bbb084942bd73ae157159441b69afe")
# Arbitrum V4
SUBGRAPH_URL = f"https://gateway.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"

def introspection_query():
    query = """
    {
      __schema {
        types {
          name
          fields {
            name
            type {
              name
              kind
            }
          }
        }
      }
    }
    """
    try:
        resp = requests.post(SUBGRAPH_URL, json={"query": query}, timeout=10)
        data = resp.json()
        if "data" in data:
            types = data['data']['__schema']['types']
            
            # Find Position type
            pos_type = next((t for t in types if t['name'] == 'Position'), None)
            if pos_type:
                print("--- Position Fields ---")
                for f in pos_type['fields']:
                    print(f.get('name'))
            else:
                print("Position type not found!")

            # Find Query type (usually matches schema queryType name, or valid root fields)
            # Actually simpler to just dump Query type if found, or 'Query' is usually the name
            query_type = next((t for t in types if t['name'] == 'Query'), None)
            if query_type:
                print("\n--- Query Root Fields ---")
                fields = [f['name'] for f in query_type['fields']]
                # Filter for modify/liquidity related
                relevant = [f for f in fields if 'modif' in f.lower() or 'liq' in f.lower()]
                print("Relevant root fields:", relevant)
                print("All root fields (first 20):", fields[:20])

    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    introspection_query()
