
import requests
import os
import json

GRAPH_API_KEY = os.getenv("GRAPH_API_KEY")
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
            
            # Find ModifyLiquidity type
            mod_type = next((t for t in types if t['name'] == 'ModifyLiquidity'), None)
            if mod_type:
                print("--- ModifyLiquidity Fields ---")
                for f in mod_type['fields']:
                    print(f.get('name'))
            else:
                print("ModifyLiquidity type not found!")

    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    introspection_query()
