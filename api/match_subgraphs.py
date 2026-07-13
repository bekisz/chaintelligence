import requests
import json

GRAPH_API_KEY = "2215756a9c5d0a9e90f0c0fcbee6730d"

# The subgraph IDs we found in the JS bundle
subgraphs = [
    "BCfy6Vw9No3weqVq9NhyGo4FkVCJep1ZN9RMJj5S32fX",
    "3V7ZY6muhxaQL5qvntX1CFXJ32W7BxXZTGTwmpH5J4t3",
    "EoCvJ5tyMLMJcTnLQwWpjAtPdn74PcrZgzfcT5bYxNBH",
    "AUpZ47RTWDBpco7YTTffGyRkBJ2i26Ms8dQSkUdxPHGc",
    "GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM",
    "8e4dRt4P4WHXnKbEq7STaQfU2g99WZ5S4w39f2PcUTjD",
    "DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G",
    "HMuAwufqZ1YCRmzL2SfHTVkzZovC9VL2UAKhjvRqKiR1",
    "HMcqgvDY6f4MpnRSJqUUsBPHePj8Hq3AxiDBfDUrWs15",
    "CjNKWQWqaVc6m1WL3CYSC4npmvG5kWBLmzFwdqCMBDoN",
    "Cvg2mRcnRKN8tFkusNGNeKPijHtm8JzTJLsnxFxStTk6",
    "7SVwgBfXoWmiK6x1NF1VEo1szkeWLniqWN1oYsX3UMb5",
    "3hCPRGf4z88VC5rsBKU5AA9FBBq5nF3jbKJG7VZCbhjm",
    "GZWDNw5b7XH2iqnmG91FLDDkfEVEDQotfPv4GMdraEKY",
    "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "8nFDCAhdnJQEhQF3ZRnfWkJ6FkRsfAiiVabVn4eGoAZH",
    "6NUtT5mGjZ1tSshKLf5Q3uEEJtjBZJo1TpL5MXsUBqrT",
    "FEtpnfQ1aqF8um2YktEkfzFD11ZKrfurvBLPeQzv9JB1",
    "DC8eAnu4QNLcWGBNkGNwcFPGXHhdzVEzBMNVMhCw8hHD",
    "ENUvnEYFET1LBjw6yDe9c9FwUpYjrrbCfvd6YHgmYzLB",
    "9TmekCrdTapofV85K3qMDMy2atpohxwrtgDYkY8JS3hL",
    "DHycyhfowfnfdxu7PNQGtzqfHUb8bzgVbgYdSxKFFULX",
    "3a88zQrAoRYVhPKzzQXwUB4ZkTHohjq1nU5onxyvsZMK",
    "6f2Z8rTvsBQinEMwRSBxbyg3BP2LTFiEA1hjPZxmy3xs",
    "7kD3yF7DsfsceFTS9syvhHLkMHCiq9Qm2zYnZShaeynb",
    "9A6bkprqEG2XsZUYJ5B2XXp6ymz9fNcn4tVPxMWDztYC",
    "41aFCdU2ofcR5KRrvFN5jxePAPjDaY2c1di5mZdmPqND",
    "4oYKWwTeEzJaFnRM7YtT2SsogtdtiWonHb3hiS3eMRWo",
    "GcDDLJEtTNdeUi72naM6izzQYajDLUDDdDr8iXwitf7v",
    "J42yJrC3wPGkr79r7MmKUHvCSN9JBdtYyWAfJr49AJfE",
]

pool_id = "0xfc7b3ad139daaf1e9c3637ed921c154d1b04286f8a82b805a6c352da57028653"

# Query to find the pool by ID in Uniswap V4 schema
v4_query = """
{
  pool(id: "%s") {
    id
    token0 { symbol }
    token1 { symbol }
    feeTier
  }
}
""" % pool_id

# Query to find the pool in Uniswap V3 schema (where id is 20 bytes, so we can check if it exists in V3)
v3_query = """
{
  pool(id: "0x65081cb48d74a32e9ccfed75164b8c09972dbcf1") {
    id
    token0 { symbol }
    token1 { symbol }
    feeTier
  }
}
"""

for sg in subgraphs:
    url = f"https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{sg}"
    # Try V4 query
    try:
        resp = requests.post(url, json={"query": v4_query}, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if 'data' in data and data['data'].get('pool') is not None:
                print(f"V4 Match! Subgraph: {sg}")
                print(json.dumps(data, indent=2))
                continue
    except Exception:
        pass

    # Try V3 query
    try:
        resp = requests.post(url, json={"query": v3_query}, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if 'data' in data and data['data'].get('pool') is not None:
                print(f"V3 Match! Subgraph: {sg}")
                print(json.dumps(data, indent=2))
                continue
    except Exception:
        pass
