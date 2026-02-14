import requests
import json
import time

BASE_URL = "https://coins.llama.fi"
address = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
coin_key = f"ethereum:{address}"

def test_params(params):
    url = f"{BASE_URL}/chart/{coin_key}"
    try:
        resp = requests.get(url, params=params, timeout=20)
        data = resp.json()
        prices = data.get("coins", {}).get(coin_key, {}).get("prices", [])
        print(f"Params: {params} -> Points: {len(prices)}")
        if prices:
            print(f"  Range: {time.ctime(prices[0]['timestamp'])} to {time.ctime(prices[-1]['timestamp'])}")
    except Exception as e:
        print(f"Error with {params}: {e}")

# Test if 'to' or 'end' works
target_end = int(time.time() - 86400 * 365) # 1 year ago
test_params({"end": target_end, "span": 100})
test_params({"to": target_end, "span": 100})
