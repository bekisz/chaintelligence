import requests
import json

URL = "http://localhost:8000/api/routes/analyze?start_token=USDC&end_token=WETH&start_date=2026-04-14&end_date=2026-04-21"
res = requests.get(URL, auth=('admin', 'chaintelligence77')).json()

for r in res['routes']:
    if 'Dynamic|v4' in r['path'] or 'Dynamic' in r['path']:
        print("Path:", r['path'])
        print("Path Tokens:", r['path_tokens'])
        print("APR:", r.get('apr'), "APR Str:", r.get('apr_str'))
        break
