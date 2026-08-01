import os
import requests
import json
from dotenv import load_dotenv

load_dotenv('.env.config')
load_dotenv('.env.secrets')

graph_api_key = os.getenv('GRAPH_API_KEY', '')

print("=== DexScreener Search for EURI/USDC ===")
try:
    url = "https://api.dexscreener.com/latest/dex/search?q=0x9d1a7a3191102e9f900faa10540837ba84dcbae7"
    r = requests.get(url, timeout=10)
    data = r.json()
    pairs = data.get("pairs", [])
    print(f"Found {len(pairs)} pairs on DexScreener for EURI:")
    for p in pairs:
        print(f"  DEX: {p.get('dexId')}, Chain: {p.get('chainId')}, PairAddress/Id: {p.get('pairAddress')}")
        print(f"  BaseToken: {p.get('baseToken', {}).get('symbol')}, QuoteToken: {p.get('quoteToken', {}).get('symbol')}")
        print(f"  Price USD: {p.get('priceUsd')}, TVL/Liquidity USD: {p.get('liquidity', {}).get('usd')}")
        print(f"  Fee / Labels: {p.get('labels')}")
        print(f"  URL: {p.get('url')}")
        print("-" * 50)
except Exception as e:
    print("DexScreener error:", e)


print("\n=== DexScreener Direct Lookup for Pool ID ===")
pool_id = "0xd85173a34c0567501850854604460efff465b40f3121b9ad17b3bcc705e083f8"
try:
    url = f"https://api.dexscreener.com/latest/dex/pairs/ethereum/{pool_id}"
    r = requests.get(url, timeout=10)
    print("Direct DexScreener pair lookup status:", r.status_code)
    print(json.dumps(r.json(), indent=2))
except Exception as e:
    print("DexScreener direct lookup error:", e)


if graph_api_key:
    print(f"\n=== Querying The Graph with GRAPH_API_KEY ({graph_api_key[:6]}...) ===")
    url = f"https://gateway-arbitrum.network.thegraph.com/api/{graph_api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
    q = f"""
    {{
      pool(id: "{pool_id}") {{
        id
        feeTier
        liquidity
        sqrtPrice
        tick
        totalValueLockedUSD
        totalValueLockedToken0
        totalValueLockedToken1
        token0 {{ symbol id decimals }}
        token1 {{ symbol id decimals }}
      }}
    }}
    """
    try:
        r = requests.post(url, json={"query": q}, timeout=10)
        print("Subgraph status:", r.status_code)
        print(json.dumps(r.json(), indent=2))
    except Exception as e:
        print("Subgraph query failed:", e)

