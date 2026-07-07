import requests

query = """
{
  pools(first: 1) {
    id
    totalValueLockedUSD
  }
}
"""

endpoints = {
    "Base V3": "https://api.studio.thegraph.com/query/48211/uniswap-v3-base/version/latest",
    "Arbitrum V3": "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-arbitrum",
}

for name, url in endpoints.items():
    try:
        r = requests.post(url, json={"query": query})
        print(f"{name}: Status {r.status_code}, Response: {r.text[:200]}")
    except Exception as e:
        print(f"{name}: Failed with {e}")
