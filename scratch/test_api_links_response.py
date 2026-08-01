import requests

try:
    # Test local API endpoints
    for url in ["http://localhost:8000/api/pools", "http://localhost:8000/api/routes?start_token=USDC&end_token=WETH"]:
        print(f"Fetching {url}...")
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                print("First item links:", data[0].get("links"))
            elif isinstance(data, dict):
                routes = data.get("routes", [])
                if len(routes) > 0:
                    hops = routes[0].get("hops", [])
                    if len(hops) > 0:
                        print("First route hop links:", hops[0].get("links"))
        else:
            print(f"Status {res.status_code}: {res.text[:200]}")
except Exception as e:
    print("Error:", e)
