#!/usr/bin/env python3
"""
Discover Uniswap V4 ModifyLiquidity Schema
The Position entity doesn't have tick data - check ModifyLiquidity instead
"""
import requests
import json

# V4 Subgraph endpoint (Arbitrum)
ENDPOINT = "https://gateway.thegraph.com/api/f4bbb084942bd73ae157159441b69afe/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r"

# Query ModifyLiquidity schema
MODIFY_LIQUIDITY_QUERY = """
{
  __type(name: "ModifyLiquidity") {
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

# Query Pool schema
POOL_QUERY = """
{
  __type(name: "Pool") {
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

def query_entity(entity_name, query):
    print(f"\n{'='*80}")
    print(f"{entity_name} Entity Schema")
    print(f"{'='*80}\n")
    
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(
            ENDPOINT,
            json={"query": query},
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if "errors" in data:
            print(f"❌ Errors: {json.dumps(data['errors'], indent=2)}")
            return
        
        entity_type = data.get("data", {}).get("__type")
        if not entity_type:
            print(f"❌ {entity_name} type not found")
            return
        
        print(f"✅ {entity_type['name']} Fields:\n")
        
        fields = entity_type.get("fields", [])
        for field in sorted(fields, key=lambda x: x["name"]):
            field_name = field["name"]
            field_type = field["type"]["name"] or field["type"].get("ofType", {}).get("name", "Unknown")
            field_kind = field["type"]["kind"]
            
            # Highlight important fields
            marker = "🎯" if any(kw in field_name.lower() for kw in ["tick", "token", "pool", "liquidity"]) else "  "
            print(f"{marker} {field_name}: {field_type} ({field_kind})")
        
    except Exception as e:
        print(f"❌ Error: {e}")

# Also try to query actual data
def query_sample_position():
    print(f"\n{'='*80}")
    print("Sample Query: Get ModifyLiquidity events for token 110050")
    print(f"{'='*80}\n")
    
    # Query for ModifyLiquidity events related to this token
    sample_query = """
    {
      modifyLiquidities(
        first: 5
        orderBy: timestamp
        orderDirection: desc
        where: { tokenId: "110050" }
      ) {
        id
        tokenId
        tickLower
        tickUpper
        liquidityDelta
        timestamp
        pool {
          id
          tick
          token0 {
            symbol
            decimals
          }
          token1 {
            symbol
            decimals
          }
        }
      }
    }
    """
    
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(
            ENDPOINT,
            json={"query": sample_query},
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if "errors" in data:
            print(f"❌ Errors: {json.dumps(data['errors'], indent=2)}")
            return
        
        events = data.get("data", {}).get("modifyLiquidities", [])
        if events:
            print(f"✅ Found {len(events)} ModifyLiquidity events:\n")
            print(json.dumps(events, indent=2))
        else:
            print("❌ No ModifyLiquidity events found for token 110050")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    query_entity("ModifyLiquidity", MODIFY_LIQUIDITY_QUERY)
    query_entity("Pool", POOL_QUERY)
    query_sample_position()
