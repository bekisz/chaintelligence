#!/usr/bin/env python3
"""
Probe the PancakeSwap V4 (Infinity) BNB subgraph.

Determines:
  1. Which endpoint works (decentralized gateway vs legacy hosted).
  2. The `swaps` entity field shape (Uniswap-V4-like vs messari-like).
  3. The swap `id` format (for log_index extraction in PostgresStorage.save_swaps).
  4. feeTier values actually in use.
  5. The pool `id` (poolId, bytes32) format.

Run:
  set -a; source .env.secrets; set +a
  python scratch/probe_pancakeswap_v4_subgraph.py
"""
import os
import sys
import json
import requests

# Candidate endpoints for PancakeSwap V4 on BNB Chain.
PLACEHOLDER_DECENTRALIZED_ID = "7XgdLW3bts4HktCYsu9dy8bEnuiNeZuftcuK3Aj4JXYV"  # from uniswap_utils.py BNB V4
HOSTED_URL = "https://api.thegraph.com/subgraphs/name/pancakeswap/pancake-v4-bsc"

GRAPH_API_KEY = os.getenv("GRAPH_API_KEY", "")


def decentralized_url(subgraph_id: str) -> str:
    if GRAPH_API_KEY:
        return f"https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{subgraph_id}"
    return f"https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/{subgraph_id}"


def post(url, query, variables=None):
    try:
        r = requests.post(url, json={"query": query, "variables": variables or {}}, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}


def try_endpoint(label, url):
    print(f"\n{'='*70}\nENDPOINT: {label}\n  {url}\n{'='*70}")
    if not GRAPH_API_KEY and "gateway-arbitrum" in url:
        print("  (no GRAPH_API_KEY in env — decentralized gateway will likely 401)")

    # 1. Schema introspection for queryable root fields + Swap/swaps entity
    intro = """
    {
      __schema {
        queryType { name }
        mutationType { name }
      }
    }"""
    res = post(url, intro)
    if "_error" in res:
        print(f"  INTROSPECT FAILED: {res['_error']}")
        return False
    if "errors" in res:
        print(f"  INTROSPECT ERRORS: {res['errors']}")
        return False
    print(f"  queryType: {res.get('data',{}).get('__schema',{}).get('queryType',{}).get('name')}")

    # 2. Dump top-level query fields (find swaps/Swap/swaps)
    fields_q = """
    {
      __type(name: "Query") {
        fields { name args { name type { name kind ofType { name kind } } } }
      }
    }"""
    res = post(url, fields_q)
    if "errors" in res or not res.get("data"):
        print(f"  QUERY-FIELDS ERRORS: {res.get('errors')}")
    else:
        fields = res["data"]["__type"]["fields"]
        names = [f["name"] for f in fields]
        print(f"  Query fields: {names}")
        swap_field = next((f for f in fields if f["name"].lower() in ("swaps", "swap")), None)
        if swap_field:
            print(f"  swap root field: {swap_field['name']}")

    # 3. Introspect the Swap entity type (try several likely names)
    for type_name in ("Swap", "SwapEvent"):
        t = post(url, f'{{ __type(name: "{type_name}") {{ fields {{ name type {{ name kind ofType {{ name kind }} }} }} }} }}')
        if t.get("data", {}).get("__type"):
            flds = t["data"]["__type"]["fields"]
            print(f"\n  {type_name} entity fields:")
            for f in flds:
                tn = f["type"]
                tname = tn.get("name") or (tn.get("ofType", {}) or {}).get("name") or "?"
                print(f"    {f['name']}: {tname} (kind={tn.get('kind')})")
            break

    # 4. Introspect Pool entity
    for type_name in ("Pool", "CLPool", "PoolInfo"):
        t = post(url, f'{{ __type(name: "{type_name}") {{ fields {{ name type {{ name kind ofType {{ name kind }} }} }} }} }}')
        if t.get("data", {}).get("__type"):
            flds = t["data"]["__type"]["fields"]
            print(f"\n  {type_name} entity fields:")
            for f in flds[:25]:
                tn = f["type"]
                tname = tn.get("name") or (tn.get("ofType", {}) or {}).get("name") or "?"
                print(f"    {f['name']}: {tname}")
            break

    # 5. Sample swaps (try both schema shapes)
    print("\n  --- sample swaps (Uniswap-V4-like shape) ---")
    q_uni = """
    {
      swaps(first: 3, orderBy: timestamp, orderDirection: desc) {
        id
        timestamp
        transaction { id }
        token0 { id symbol }
        token1 { id symbol }
        amount0
        amount1
        amountUSD
        pool { id feeTier }
      }
    }"""
    res = post(url, q_uni)
    if res.get("errors"):
        print(f"    uni-shape errors: {res['errors']}")
    else:
        swaps = res.get("data", {}).get("swaps", [])
        print(f"    got {len(swaps)} swaps (uni-shape)")
        if swaps:
            print("    sample[0]:")
            print(json.dumps(swaps[0], indent=2)[:1200])

    print("\n  --- sample swaps (messari-like shape) ---")
    q_mes = """
    {
      swaps(first: 3, orderBy: timestamp, orderDirection: desc) {
        id
        hash
        timestamp
        tokenIn { id symbol }
        tokenOut { id symbol }
        amountIn
        amountOut
        amountInUSD
        pool { id name fees { feePercentage } inputTokens { id symbol } }
      }
    }"""
    res = post(url, q_mes)
    if res.get("errors"):
        print(f"    messari-shape errors: {res['errors']}")
    else:
        swaps = res.get("data", {}).get("swaps", [])
        print(f"    got {len(swaps)} swaps (messari-shape)")
        if swaps:
            print("    sample[0]:")
            print(json.dumps(swaps[0], indent=2)[:1200])

    # 6. Sample pools to see feeTier values + poolId format
    print("\n  --- sample pools (feeTier distribution + id format) ---")
    q_pools = """
    {
      pools(first: 10, orderBy: totalValueLockedUSD, orderDirection: desc) {
        id
        feeTier
        token0 { id symbol }
        token1 { id symbol }
        totalValueLockedUSD
      }
    }"""
    res = post(url, q_pools)
    if res.get("errors"):
        print(f"    pools errors: {res['errors']}")
    else:
        pools = res.get("data", {}).get("pools", [])
        print(f"    got {len(pools)} pools")
        for p in pools[:10]:
            print(f"      id={p.get('id')} fee={p.get('feeTier')} "
                  f"t0={p.get('token0',{}).get('symbol')} t1={p.get('token1',{}).get('symbol')} "
                  f"tvl={p.get('totalValueLockedUSD')}")
    return True


def main():
    print(f"GRAPH_API_KEY: {'set (' + GRAPH_API_KEY[:6] + '...)' if GRAPH_API_KEY else 'NOT SET'}")

    worked = try_endpoint("decentralized (placeholder ID)", decentralized_url(PLACEHOLDER_DECENTRALIZED_ID))
    try_endpoint("legacy hosted", HOSTED_URL)

    print("\n" + "=" * 70)
    print("PROBE COMPLETE — review output to choose endpoint + query shape.")
    print("=" * 70)


if __name__ == "__main__":
    main()
