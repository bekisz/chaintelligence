import requests, os
from dotenv import load_dotenv
import json

os.environ["AIRFLOW_HOME"] = "/opt/airflow"
load_dotenv("/opt/airflow/.env.secrets")

API = os.getenv("GRAPH_API_KEY")
URL = f"https://gateway-arbitrum.network.thegraph.com/api/{API}/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

euri = "0xf23351d4289cf30113a34a81b7e42be005232ba3".lower()
usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48".lower()

q = f"""
{{
  pools(where: {{ token0: "{euri}", token1: "{usdc}" }}) {{
    id
    feeTier
    token0 {{ symbol }}
    token1 {{ symbol }}
  }}
}}
"""
res = requests.post(URL, json={'query': q}).json()
print("EURI, USDC:")
print(json.dumps(res, indent=2))

q2 = f"""
{{
  pools(where: {{ token0: "{usdc}", token1: "{euri}" }}) {{
    id
    feeTier
    token0 {{ symbol }}
    token1 {{ symbol }}
  }}
}}
"""
res2 = requests.post(URL, json={'query': q2}).json()
print("USDC, EURI:")
print(json.dumps(res2, indent=2))
