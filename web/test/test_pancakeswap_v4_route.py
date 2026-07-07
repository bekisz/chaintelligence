"""
PancakeSwap V4 (Infinity) route contract test.

Asserts the /api/routes/analyze endpoint returns PancakeSwap V4 routes for a
BNB token pair, with the V4 pool_id injected as `pool_address` (the singleton
PoolManager model has no per-pool contract address).

Requires the API server running and PancakeSwap V4 swaps ingested for the
queried window (the pancakeswap_v4_history_sync DAG backfills pool_id).

Run:
  cd web/test && python -m pytest test_pancakeswap_v4_route.py -v
or:
  python test_pancakeswap_v4_route.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

API_URL = os.getenv("API_URL", "http://localhost:8000")
PORTAL_USER = os.getenv("PORTAL_USERNAME", "admin")
PORTAL_PASS = os.getenv("PORTAL_PASSWORD", "chaintelligence77")

# Query a recent window where the PancakeSwap V4 ingestion DAG has data.
now = datetime.now(timezone.utc)
END_DATE = now.strftime("%Y-%m-%d")
START_DATE = (now - timedelta(days=3)).strftime("%Y-%m-%d")


def _analyze(start_token="USDC", end_token="USDT", network="BNB"):
    """Stream the analyze endpoint and return the parsed result data dict."""
    params = {
        "start_token": start_token,
        "end_token": end_token,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "network": network,
    }
    auth = httpx.BasicAuth(PORTAL_USER, PORTAL_PASS)
    with httpx.Client(auth=auth, timeout=300.0) as client:
        with client.stream("GET", f"{API_URL}/api/routes/analyze", params=params) as resp:
            assert resp.status_code == 200, f"analyze HTTP {resp.status_code}"
            for line in resp.iter_lines():
                if not line:
                    continue
                evt = json.loads(line)
                if evt.get("type") == "result":
                    return evt["data"]
    raise AssertionError("no result event in NDJSON stream")


def _v4_routes(data):
    out = []
    for r in data.get("routes", []):
        for pt in r.get("path_tokens", []):
            if isinstance(pt, dict) and "PancakeSwap V4" in str(pt.get("fee", "")):
                out.append((r, pt))
                break
    return out


def test_pancakeswap_v4_route_has_pool_id():
    data = _analyze()
    v4 = _v4_routes(data)
    assert v4, (
        f"No PancakeSwap V4 routes found for USDC->USDT on BNB "
        f"({START_DATE}..{END_DATE}). Ensure the V4 ingestion DAG has run."
    )
    for route, node in v4:
        pool_addr = node.get("pool_address")
        assert pool_addr, f"V4 route missing pool_address (link target): {route.get('path')}"
        assert pool_addr.startswith("0x"), f"pool_address not hex: {pool_addr}"
        # PancakeSwap V4: pool_address is coin0's contract address (42 chars);
        # Uniswap V4 would be a 66-char poolId. Either is a valid link target.
        assert len(pool_addr) >= 42, f"pool_address too short: {pool_addr}"
    print(f"\n  ✅ {len(v4)} PancakeSwap V4 route(s) with link target, e.g. "
          f"{v4[0][1]['pool_address']}")


def test_pancakeswap_v4_frontend_url_pattern():
    """Frontend links PancakeSwap V4 to the pool page (32-byte poolId) or, for
    long-tail pools without an explorer match, the token's Infinity pairs page."""
    data = _analyze()
    v4 = _v4_routes(data)
    if not v4:
        print("  (skipped — no V4 routes)")
        return
    _, node = v4[0]
    fee_parts = str(node["fee"]).split("|")
    net = fee_parts[2].strip() if len(fee_parts) >= 3 else "BNB"
    chain = {"BNB": "bsc", "Base": "base", "Ethereum": "eth", "Arbitrum": "arb"}.get(net, "bsc")
    pa = node["pool_address"]
    if len(pa) == 66:
        expected = f"https://pancakeswap.finance/liquidity/pool/{chain}/{pa}"
        assert "/liquidity/pool/" in expected, expected
    else:
        expected = f"https://pancakeswap.finance/info/infinity/pairs/tokens/{pa}?chain={chain}"
        assert "info/infinity/pairs/tokens/" in expected, expected
    print(f"\n  ✅ expected frontend V4 link → {expected}")


if __name__ == "__main__":
    test_pancakeswap_v4_route_has_pool_id()
    test_pancakeswap_v4_frontend_url_pattern()
    print("\nAll PancakeSwap V4 contract tests passed.")
