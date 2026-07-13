import requests
import json

pool_address = "0xfc7b3ad139daaf1e9c3637ed921c154d1b04286f8a82b805a6c352da57028653"
non_working = "0xfab5489f087071d6d475fca0bd4be14884ff59a6e484545951a26922df31391f"

urls = [
    # Uniswap V4 endpoints
    f"https://api.revert.finance/uniswapv4/arbitrum/pool/{pool_address}",
    f"https://api.revert.finance/uniswapv4/arbitrum/pools/{pool_address}",
    f"https://api.revert.finance/v4/arbitrum/pool/{pool_address}",
    f"https://api.revert.finance/arbitrum/uniswapv4/pool/{pool_address}",
    
    # Uniswap V3 endpoints as reference/guess
    f"https://api.revert.finance/uniswapv3/arbitrum/pool/{pool_address}",
    f"https://api.revert.finance/uniswapv3/arbitrum/pools/{pool_address}",
    f"https://api.revert.finance/v3/arbitrum/pool/{pool_address}",
    f"https://api.revert.finance/arbitrum/uniswapv3/pool/{pool_address}",
    
    # Other guesses
    f"https://api.revert.finance/pool/arbitrum/uniswapv4/{pool_address}",
    f"https://api.revert.finance/pool/arbitrum/uniswapv3/{pool_address}",
]

for url in urls:
    try:
        resp = requests.get(url, timeout=5)
        print(f"URL: {url} -> Status: {resp.status_code}")
        if resp.status_code == 200:
            print(resp.text[:500])
    except Exception as e:
        print(f"URL: {url} -> Error: {e}")
