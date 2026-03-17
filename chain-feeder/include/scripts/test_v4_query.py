import requests, os
from dotenv import load_dotenv

os.environ["AIRFLOW_HOME"] = "/opt/airflow"
load_dotenv("/opt/airflow/.env.secrets")

API = os.getenv("GRAPH_API_KEY")
URL = f"https://gateway-arbitrum.network.thegraph.com/api/{API}/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

euri = "0xf23351d4289cf30113a34a81b7e42be005232ba3"
usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

# Remember sorted order:
t0, t1 = sorted([euri, usdc])

q = f"""
{{
  pools(where: {{ token0: "{t0}", token1: "{t1}", feeTier: 3000 }}) {{
    id
    feeTier
    poolDayData(first: 5) {{
      date
      tvlUSD
      volumeUSD
    }}
  }}
}}
"""

res = requests.post(URL, json={'query': q}).json()
import json
print(json.dumps(res, indent=2))
