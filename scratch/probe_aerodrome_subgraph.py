#!/usr/bin/env python3
"""
Probe the Aerodrome (Base) subgraph.

Determines:
  1. Which endpoint/deployment ID works (decentralized gateway vs legacy hosted).
  2. The `swaps` entity field shape (Uniswap-like vs messari-like vs velodrome-like).
  3. The swap `id` format (for log_index extraction in PostgresStorage.save_swaps).
  4. The pool/pair entity + fee/volatility fields (Aerodrome uses stable/volatile pools).

Candidate deployment IDs come from The Graph explorer search and must be verified
live — do NOT hardcode them into the fetcher until this probe confirms one works.

Run:
  set -a; source .env.secrets; set +a
  python scratch/probe_aerodrome_subgraph.py
"""
import os
import json
import requests

# Candidate deployment IDs to verify. The Graph explorer lists several
# Aerodrome subgraphs; we try each until one responds.
CANDIDATE_DECENTRALIZED_IDS = [
    "nZnftbmERiB2tY6t2ika7kPsrTcKnYFEnqG3RKa38r",  # claimed "base-v3-aerodrome"
    "GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM",  # claimed "Aerodrome Base Full"
]
HOSTED_URL = "https://api.thegraph.com/subgraphs/name/aerodrome/base"

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

    # 1. Schema introspection — queryType
    intro = "{ __schema { queryType { name } } }"
    res = post(url, intro)
    if "_error" in res:
        print(f"  INTROSPECT FAILED: {res['_error']}")
        return False
    if "errors" in res:
        print(f"  INTROSPECT ERRORS: {res['errors']}")
        return False
    print(f"  queryType: {res.get('data',{}).get('__schema',{}).get('queryType',{}).get('name')}")

    # 2. Top-level query fields (find swaps/Swap)
    fields_q = "{ __type(name: \"Query\") { fields { name } } }"
    res = post(url, fields_q)
    if res.get("errors") or not res.get("data") or not res["data"].get("__type"):
        print(f"  QUERY-FIELDS ERRORS: {res.get('errors')}")
    else:
        names = [f["name"] for f in res["data"]["__type"]["fields"]]
        print(f"  Query fields: {names}")

    # 3. Introspect the Swap entity (try several likely names)
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

    # 4. Introspect pool/pair entity (Aerodrome is V2-fork → "Pair" likely)
    for type_name in ("Pair", "Pool", "LiquidityPool"):
        t = post(url, f'{{ __type(name: "{type_name}") {{ fields {{ name type {{ name kind ofType {{ name kind }} }} }} }} }}')
        if t.get("data", {}).get("__type"):
            flds = t["data"]["__type"]["fields"]
            print(f"\n  {type_name} entity fields:")
            for f in flds[:25]:
                tn = f["type"]
                tname = tn.get("name") or (tn.get("ofType", {}) or {}).get("name") or "?"
                print(f"    {f['name']}: {tname}")
            break

    # 5. Sample swaps — try three schema shapes
    shapes = {
        "uni-like": """
        {
          swaps(first: 3, orderBy: timestamp, orderDirection: desc) {
            id timestamp transaction { id }
            token0 { id symbol } token1 { id symbol }
            amount0 amount1 amountUSD pool { id feeTier }
          }
        }""",
        "messari-like": """
        {
          swaps(first: 3, orderBy: timestamp, orderDirection: desc) {
            id hash timestamp
            tokenIn { id symbol } tokenOut { id symbol }
            amountIn amountOut amountInUSD
            pool { id name fees { feePercentage } inputTokens { id symbol } }
          }
        }""",
        "velodrome-like": """
        {
          swaps(first: 3, orderBy: timestamp, orderDirection: desc) {
            id timestamp transaction { id }
            pair { id token0 { id symbol } token1 { id symbol } isStable }
            token0 { id symbol } token1 { id symbol }
            amount0In amount0Out amount1In amount1Out
          }
        }""",
    }
    for label, q in shapes.items():
        print(f"\n  --- sample swaps ({label}) ---")
        res = post(url, q)
        if res.get("errors"):
            print(f"    {label} errors: {res['errors']}")
        else:
            swaps = res.get("data", {}).get("swaps", [])
            print(f"    got {len(swaps)} swaps ({label})")
            if swaps:
                print("    sample[0]:")
                print(json.dumps(swaps[0], indent=2)[:1200])

    return True


def main():
    print(f"GRAPH_API_KEY: {'set (' + GRAPH_API_KEY[:6] + '...)' if GRAPH_API_KEY else 'NOT SET'}")

    worked = False
    for sid in CANDIDATE_DECENTRALIZED_IDS:
        if try_endpoint(f"decentralized ({sid[:10]}...)", decentralized_url(sid)):
            worked = True
    try_endpoint("legacy hosted", HOSTED_URL)

    print("\n" + "=" * 70)
    print("PROBE COMPLETE — review output to choose endpoint + query shape.")
    print("If no endpoint worked, find the live Aerodrome subgraph ID on")
    print("https://thegraph.com/explorer and add it to CANDIDATE_DECENTRALIZED_IDS.")
    print("=" * 70)


if __name__ == "__main__":
    main()
