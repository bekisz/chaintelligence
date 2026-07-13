import requests
import json

GRAPH_API_KEY = "a09146d9b04d58e07e68bbdca38aa54e"
v4_subgraph_id = "G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"
url = f"https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{v4_subgraph_id}"

tokens = [
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1", # WETH
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", # USDC
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8", # USDC.e
]

query = """
{
  pools(first: 100, where: {
    token0_in: %s,
    token1_in: %s
  }) {
    id
    token0 { symbol id }
    token1 { symbol id }
    feeTier
    tickSpacing
    hooks
    liquidity
  }
}
""" % (json.dumps(tokens), json.dumps(tokens))

resp = requests.post(url, json={"query": query}, timeout=20)
print(f"Status: {resp.status_code}")
data = resp.json()
if 'data' in data and data['data']:
    pools = data['data'].get('pools', [])
    print(f"Found {len(pools)} pools:")
    for p in pools:
        print(f"  Pool ID: {p['id']}")
        print(f"    Token0: {p['token0']['symbol']} ({p['token0']['id']})")
        print(f"    Token1: {p['token1']['symbol']} ({p['token1']['id']})")
        print(f"    FeeTier: {p['feeTier']}")
        print(f"    TickSpacing: {p['tickSpacing']}")
        print(f"    Hooks: {p['hooks']}")
        print(f"    Liquidity: {p['liquidity']}")
        print(f"    Revert url: https://revert.finance/#/pool/arbitrum/uniswapv4/{p['id']}")
else:
    print(json.dumps(data, indent=2))
