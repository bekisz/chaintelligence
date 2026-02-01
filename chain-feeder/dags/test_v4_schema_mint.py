import os
import sys
import logging
import requests
import json

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from uniswap_v4_range_fetcher import UNISWAP_V4_URL_TEMPLATE

logging.basicConfig(level=logging.DEBUG)

def test_schema_mint():
    key = os.environ.get("GRAPH_API_KEY")
    if not key: return

    endpoint = UNISWAP_V4_URL_TEMPLATE.format(api_key=key)
    
    query = """
    {
      Type_Mint: __type(name: "Mint") {
         name
         fields { name }
      }
    }
    """
    
    try:
        resp = requests.post(endpoint, json={"query": query}, headers={"Content-Type": "application/json"})
        data = resp.json()
        print(json.dumps(data, indent=2))
    except Exception as e:
        print(e)
        
if __name__ == "__main__":
    test_schema_mint()
