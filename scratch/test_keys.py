import requests

keys = [
    "a09146d9b04d58e07e68bbdca38aa54e",
    "f4bbb084942bd73ae157159441b69afe"
]

query = """
{
  pools(first: 1) {
    id
  }
}
"""

# Ethereum V3 subgraph
sg_id = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"

for key in keys:
    url = f"https://gateway-arbitrum.network.thegraph.com/api/{key}/subgraphs/id/{sg_id}"
    try:
        r = requests.post(url, json={"query": query})
        print(f"Key {key}: Status {r.status_code}, Response: {r.text[:200]}")
    except Exception as e:
        print(f"Key {key}: Failed with {e}")
