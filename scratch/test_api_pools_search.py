import requests
import json

url = "http://localhost:8000/api/pools/search?start_token=WETH&end_token=USDC&days=30"
auth = ("admin", "chaintelligence77")

print(f"Requesting {url}...")
res = requests.get(url, auth=auth, timeout=10)
print(f"Status: {res.status_code}")
if res.status_code == 200:
    for line in res.text.splitlines():
        if line.strip():
            msg = json.loads(line)
            if msg.get("type") == "result":
                pools = msg["data"]["pools"]
                print(f"Found {len(pools)} pools!")
                if len(pools) > 0:
                    print("\nFirst pool links payload:")
                    for k, v in pools[0]["links"].items():
                        print(f"  {k}: {v}")
