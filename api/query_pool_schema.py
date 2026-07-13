import requests
import json

url = "https://gateway-arbitrum.network.thegraph.com/api/2215756a9c5d0a9e90f0c0fcbee6730d/subgraphs/id/GZWDNw5b7XH2iqnmG91FLDDkfEVEDQotfPv4GMdraEKY"

# Query schema for Pool type fields
query = """
{
  __type(name: "Pool") {
    fields {
      name
      type {
        name
        kind
      }
    }
  }
}
"""

try:
    resp = requests.post(url, json={"query": query}, timeout=10)
    print(json.dumps(resp.json(), indent=2))
except Exception as e:
    print(f"Error: {e}")
