#!/usr/bin/env python3
"""
Inspect Transaction Type and Verify Linking
"""
import requests
import json

ENDPOINT = "https://gateway.thegraph.com/api/YOUR_API_KEY/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"

# 1. Introspect Transaction Type
INTROSPECTION_QUERY = """
{
  __type(name: "Transaction") {
    name
    fields {
      name
      type {
        name
        kind
        ofType {
          name
          kind
        }
      }
    }
  }
}
"""

def inspect_schema():
    print(f"\n{'='*80}")
    print("Transaction Schema")
    print(f"{'='*80}\n")
    try:
        response = requests.post(ENDPOINT, json={"query": INTROSPECTION_QUERY}, timeout=10)
        data = response.json()
        fields = data.get("data", {}).get("__type", {}).get("fields", [])
        for f in sorted(fields, key=lambda x: x["name"]):
            print(f"{f['name']}: {f['type']['name']}")
    except Exception as e:
        print(f"Error: {e}")

# 2. Try to link Position -> Transfers -> Transaction -> (Query ModifyLiquidity by TX)
def try_linking():
    print(f"\n{'='*80}")
    print("Try Linking Position -> Transfer -> Transaction -> ModifyLiquidities")
    print(f"{'='*80}\n")
    
    # query Position -> Transfers -> Transaction ID
    pos_query = """
    {
      position(id: "110050") {
        id
        tokenId
        transfers {
          transaction {
            id
          }
        }
      }
    }
    """
    
    try:
        resp = requests.post(ENDPOINT, json={"query": pos_query}, timeout=10)
        data = resp.json()
        
        pos = data.get("data", {}).get("position")
        if not pos:
            print("Position 110050 not found.")
            return

        transfers = pos.get("transfers", [])
        if not transfers:
            print("No transfers found for position.")
            return
            
        print(f"Found {len(transfers)} transfers.")
        
        # Take the first transfer (likely MINT)
        tx_id = transfers[0].get("transaction", {}).get("id")
        print(f"Transaction ID: {tx_id}")
        
        if tx_id:
            # Query ModifyLiquidity filtering by this TX
            filter_query = f'''
            {{
              modifyLiquidities(where: {{ transaction: "{tx_id}" }}) {{
                id
                tickLower
                tickUpper
                pool {{
                  tick
                  token0 {{ symbol decimals }}
                  token1 {{ symbol decimals }}
                  feeTier
                }}
              }}
            }}
            '''
            
            resp2 = requests.post(ENDPOINT, json={"query": filter_query}, timeout=10)
            data2 = resp2.json()
            mods = data2.get("data", {}).get("modifyLiquidities", [])
            print(f"Found {len(mods)} ModifyLiquidity events in transaction {tx_id}:")
            print(json.dumps(mods, indent=2))
            
    except Exception as e:
        print(f"Error linking: {e}")

if __name__ == "__main__":
    inspect_schema()
    try_linking()
