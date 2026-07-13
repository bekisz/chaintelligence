import requests

url = "https://gateway-arbitrum.network.thegraph.com/api/a09146d9b04d58e07e68bbdca38aa54e/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

query = """
query GetTokens {
  t0: token(id: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48") { symbol, decimals }
  t1: token(id: "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2") { symbol, decimals }
}
"""

res = requests.post(url, json={'query': query})
print(res.json())
