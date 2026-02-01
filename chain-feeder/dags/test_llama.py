import requests
import json

def check():
    c0 = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48" # USDC
    c1 = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2" # WETH
    
    url = f"https://coins.llama.fi/prices/current/ethereum:{c0},ethereum:{c1}"
    print("URL:", url)
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=5)
        print("Status:", resp.status_code)
        print("Data:", resp.text[:200])
        
        data = resp.json()["coins"]
        p0 = data.get(f"ethereum:{c0}", {}).get("price")
        p1 = data.get(f"ethereum:{c1}", {}).get("price")
        print(f"USDC: {p0}, WETH: {p1}")
        
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    check()
