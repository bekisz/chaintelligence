import requests
import time
import urllib.parse
from requests.auth import HTTPBasicAuth

url = "http://localhost:8000/api/routes/analyze?start_token=USDT&end_token=USDC&network=Ethereum&days=7"
start = time.time()
res = requests.get(url, auth=HTTPBasicAuth("admin", "chaintelligence77"))
duration = time.time() - start

print(f"Status: {res.status_code}")
if res.status_code == 200:
    data = res.json()
    print(f"Routes found: {len(data.get('routes', []))}")
    print(f"Time taken: {duration:.2f} seconds")
else:
    print(res.text)
