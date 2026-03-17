import requests
import json

graph_api_key = "a09146d9b04d58e07e68bbdca38aa54e"
ENDPOINT_V4 = f"https://gateway-arbitrum.network.thegraph.com/api/{graph_api_key}/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

def check_meta():
    q = "{ _meta { block { hash number } } }"
    resp = requests.post(ENDPOINT_V4, json={"query": q})
    print(resp.json())

if __name__ == "__main__":
    check_meta()
