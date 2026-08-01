import requests
import json

url = "http://localhost:8000/api/routes/analyze?start_token=WETH&end_token=USDC&days=30"
auth = ("admin", "chaintelligence77")

print(f"Requesting {url}...")
res = requests.get(url, auth=auth, timeout=10)
print(f"Status: {res.status_code}")
if res.status_code == 200:
    for line in res.text.splitlines():
        if line.strip():
            msg = json.loads(line)
            if msg.get("type") == "result":
                routes = msg["data"]["routes"]
                print(f"Found {len(routes)} routes!")
                if len(routes) > 0:
                    first_route = routes[0]
                    path = first_route.get("path", [])
                    print(f"Path elements: {len(path)}")
                    for idx, item in enumerate(path):
                        if isinstance(item, dict):
                            print(f"  Hop dict links payload at index {idx}:")
                            for k, v in item.get("links", {}).items():
                                print(f"    {k}: {v}")
