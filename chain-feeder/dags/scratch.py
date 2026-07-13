import requests
url = "https://gateway-arbitrum.network.thegraph.com/api/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"
q = """{
  positions(where: {owner: "0x78d6f68df933995aa1b6840eacfa12e4759b0e13"}, first: 5) {
    id
    tickLower
    tickUpper
    pool { tick }
  }
}"""
r = requests.post(url, json={"query": q})
print(r.json())
