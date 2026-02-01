import os
import sys
import logging
import requests
import json

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from uniswap_v4_range_fetcher import UNISWAP_V4_URL_TEMPLATE

logging.basicConfig(level=logging.DEBUG)

def test_introspection():
    key = os.environ.get("GRAPH_API_KEY")
    if not key:
        print("ERROR: No GRAPH_API_KEY")
        return

    endpoint = UNISWAP_V4_URL_TEMPLATE.format(api_key=key)
    
    query = """
    {
      Position: __type(name: "Position") {
        name
        fields {
          name
        }
      }
      Pool: __type(name: "Pool") {
        name
        fields {
          name
        }
      }
    }
    """
    
    try:
        print("Running Introspection...")
        resp = requests.post(
            endpoint,
            json={"query": query},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        data = resp.json()
        if "errors" in data:
            print("Errors:", data["errors"])
        else:
            print(json.dumps(data, indent=2))
            
    except Exception as e:
        print("Exception:", e)

if __name__ == "__main__":
    test_introspection()
