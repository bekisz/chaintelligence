
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
        }
      }
    }
    """
    try:
        resp = requests.post(SUBGRAPH_URL, json={"query": query}, timeout=10)
        data = resp.json()
        if "data" in data:
            types = data['data']['__schema']['types']
            names = [t['name'] for t in types if not t['name'].startswith('__')]
            print("--- All Types ---")
            for n in sorted(names):
                print(n)
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    introspection_query()
