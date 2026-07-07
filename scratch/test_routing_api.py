import requests
import json

url = "http://localhost:8000/api/routes/analyze?network=BNB"
payload = {
    "start_token": "BNB",
    "end_token": "USDT",
    "days": 5,
    "max_hops": 3
}

res = requests.post(url, json=payload)
print("STATUS CODE:", res.status_code)
try:
    routes = res.json()
    print(f"Found {len(routes)} routes!")
    if routes:
        print("First route:")
        print(json.dumps(routes[0], indent=2))
except Exception as e:
    print("ERROR:", e)
    print("TEXT:", res.text)
