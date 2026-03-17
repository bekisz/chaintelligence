import requests, os
from dotenv import load_dotenv

os.environ["AIRFLOW_HOME"] = "/opt/airflow"
load_dotenv("/opt/airflow/.env.secrets")

API = os.getenv("GRAPH_API_KEY")
URL = f"https://gateway-arbitrum.network.thegraph.com/api/{API}/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

q = "{ __schema { types { name fields { name } } } }"
res = requests.post(URL, json={'query': q}).json()
types = res['data']['__schema']['types']

pool_day_data = next((t for t in types if t['name'] == 'PoolDayData'), None)
if pool_day_data:
    print("PoolDayData fields:")
    for f in pool_day_data.get('fields', []):
        print(f" - {f['name']}")
else:
    print("PoolDayData not found")
