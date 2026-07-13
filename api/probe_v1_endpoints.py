import requests
import json

endpoints = [
    "https://api.revert.finance/v1/pools/arbitrum",
    "https://api.revert.finance/v1/uniswapv3/pools",
    "https://api.revert.finance/v1/uniswapv3/arbitrum/pools",
    "https://api.revert.finance/v1/uniswapv4/arbitrum/pools",
    "https://api.revert.finance/v1/arbitrum/uniswapv3/pools",
    "https://api.revert.finance/v1/arbitrum/uniswapv4/pools",
    "https://api.revert.finance/v1/pools",
]

for ep in endpoints:
    try:
        resp = requests.get(ep, timeout=5)
        print(f"EP: {ep} -> Status: {resp.status_code}")
        if resp.status_code == 200:
            print(resp.text[:500])
    except Exception as e:
        print(f"EP: {ep} -> Error: {e}")
