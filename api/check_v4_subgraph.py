"""Check Uniswap V4 pool IDs on Arbitrum via the subgraph."""
import requests
import json

# Uniswap V4 subgraph on Arbitrum
SUBGRAPH_URL = "https://gateway.thegraph.com/api/subgraphs/id/FbCGRftH4a3yZugY7TnbYgPJVEv2LvMT6oF1fxPe9aJM"

# Try public endpoint first
PUBLIC_URL = "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v4-arbitrum"

QUERY = """
{
  pools(first: 20, where: {
    token0_in: ["0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"],
    token1_in: ["0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"]
  }, orderBy: volumeUSD, orderDirection: desc) {
    id
    token0 { symbol }
    token1 { symbol }
    feeTier
    volumeUSD
  }
}
"""

# Try a different known source: the Uniswap official subgraph  
resp = requests.post(PUBLIC_URL, json={"query": QUERY}, timeout=20)
print(f"Status: {resp.status_code}")
try:
    data = resp.json()
    if 'data' in data and data['data']:
        pools = data['data'].get('pools', [])
        print(f"Found {len(pools)} pools")
        for p in pools:
            print(f"  ID={p['id']} {p['token0']['symbol']}/{p['token1']['symbol']} fee={p['feeTier']} vol={p.get('volumeUSD','?')}")
    else:
        print(json.dumps(data, indent=2)[:2000])
except Exception as e:
    print(f"Error: {e}")
    print(resp.text[:1000])
