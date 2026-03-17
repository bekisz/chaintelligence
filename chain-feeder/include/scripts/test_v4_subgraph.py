import requests
import json
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '../../../../.env.config'))
load_dotenv(os.path.join(os.path.dirname(__file__), '../../../../.env.secrets'))

API_KEY = os.getenv('GRAPH_API_KEY')
if not API_KEY:
    print("No GRAPH_API_KEY found")
    exit(1)

# V4 Subgraph ID from earlier search
SUBGRAPH_ID = "DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"
URL = f"https://gateway-arbitrum.network.thegraph.com/api/{API_KEY}/subgraphs/id/{SUBGRAPH_ID}"

query = """
{
  __schema {
    types {
      name
      fields {
        name
      }
    }
  }
}
"""

req = requests.post(URL, json={'query': query})
data = req.json()

if 'errors' in data:
    print(data)
else:
    types = data['data']['__schema']['types']
    swap_type = next((t for t in types if t['name'] == 'Swap'), None)
    if swap_type:
        print("Swap fields:")
        for f in swap_type['fields']:
            print(f"- {f['name']}")
    else:
        print("No Swap type found. Types:")
        print([t['name'] for t in types if t['name'] and not t['name'].startswith('_')])

    pool_type = next((t for t in types if t['name'] == 'Pool'), None)
    if pool_type:
        print("Pool fields:")
        for f in pool_type['fields']:
            print(f"- {f['name']}")

    print("\nTrying to fetch 1 swap with proper pool fields...")
    swap_q = """
    {
      swaps(first: 1) {
        id
        timestamp
        token0 { id symbol }
        token1 { id symbol }
        amount0
        amount1
        pool { id feeTier }
      }
    }
    """
    swap_res = requests.post(URL, json={'query': swap_q}).json()
    print(json.dumps(swap_res, indent=2))
