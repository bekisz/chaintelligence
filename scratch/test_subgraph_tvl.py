import os
import requests
from datetime import datetime, timezone, timedelta

def main():
    GRAPH_API_KEY = os.getenv('GRAPH_API_KEY', '')
    print(f"GRAPH_API_KEY: {GRAPH_API_KEY}")

    # Uniswap V3 subgraph ID for Ethereum
    v3_subgraph_id = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"

    if not GRAPH_API_KEY or GRAPH_API_KEY == 'YOUR_GRAPH_API_KEY':
        url = f'https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/{v3_subgraph_id}'
    else:
        url = f'https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{v3_subgraph_id}'

    # BTT address: 0xc669928185dbce49d2230cc9b0979be6dc797957
    # WETH address: 0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2
    addr0 = "0xc669928185dbce49d2230cc9b0979be6dc797957".lower()
    addr1 = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2".lower()
    t0, t1 = sorted([addr0, addr1])
    fee_tier_bips = 3000

    query = f"""
    {{
      pools(where: {{
        token0: "{t0}", 
        token1: "{t1}", 
        feeTier: "{fee_tier_bips}"
      }}) {{
        id
        totalValueLockedToken0
        totalValueLockedToken1
        totalValueLockedUSD
      }}
    }}
    """
    
    print(f"Querying URL: {url.replace(GRAPH_API_KEY, '***') if GRAPH_API_KEY else url}")
    res = requests.post(url, json={'query': query})
    print(f"Status: {res.status_code}")
    print(res.text)

if __name__ == "__main__":
    main()
