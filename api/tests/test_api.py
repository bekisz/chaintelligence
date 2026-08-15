import requests
import os
import unittest
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BASE_URL = os.getenv("API_URL", "http://localhost:8000")
USERNAME = os.getenv("PORTAL_USERNAME", "admin")
PASSWORD = os.getenv("PORTAL_PASSWORD", "chaintelligence")

class TestChaintelligenceAPI(unittest.TestCase):
    def setUp(self):
        self.auth = (USERNAME, PASSWORD)

    def test_01_health(self):
        """Test /health endpoint (no auth required) — first test, always hit."""
        url = f"{BASE_URL}/health"
        response = requests.get(url)
        self.assertEqual(response.status_code, 200, f"Health check failed: {response.text}")
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("database", data)
        self.assertIn("timestamp", data)

    def test_02_coin_list(self):
        """Test public endpoint: /api/coins/list"""
        url = f"{BASE_URL}/api/coins/list"
        response = requests.get(url)
        self.assertEqual(response.status_code, 200, f"Failed to fetch coin list: {response.text}")
        data = response.json()
        self.assertIsInstance(data, list)
        if len(data) > 0:
            self.assertIn("symbol", data[0])
            self.assertIn("name", data[0])

    def test_03_price_history(self):
        """Test public endpoint: /api/coin/price-history"""
        # Testing with ETH which is likely to exist
        url = f"{BASE_URL}/api/coin/price-history?symbol=ETH"
        response = requests.get(url)
        self.assertEqual(response.status_code, 200, f"Failed to fetch price history: {response.text}")
        data = response.json()
        self.assertEqual(data["symbol"], "ETH")
        self.assertIn("data", data)
        self.assertIsInstance(data["data"], list)

    def test_04_date_range(self):
        """Test protected endpoint: /api/routes/date-range"""
        url = f"{BASE_URL}/api/routes/date-range"
        # Test without auth
        response_no_auth = requests.get(url)
        self.assertEqual(response_no_auth.status_code, 401, "Protected endpoint allowed access without auth")
        
        # Test with auth
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed to fetch date range: {response.text}")
        data = response.json()
        self.assertIn("min_date", data)
        self.assertIn("max_date", data)

    def test_05_lp_summary(self):
        """Test protected endpoint: /api/lp/position-summary"""
        url = f"{BASE_URL}/api/lp/position-summary"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed to fetch LP summary: {response.text}")
        data = response.json()
        self.assertIsInstance(data, list)

    def test_06_analyze_routes(self):
        """Test protected endpoint: /api/routes/analyze"""
        import json
        # Test a common pair
        url = f"{BASE_URL}/api/routes/analyze?start_token=ETH&end_token=USDC&days=1"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Analysis failed: {response.text}")
        
        result_data = None
        for line in response.iter_lines():
            if line:
                chunk = json.loads(line.decode('utf-8'))
                if chunk.get("type") == "result":
                    result_data = chunk.get("data")
                    break
                    
        self.assertIsNotNone(result_data, "No result block found in analyze stream")
        self.assertIn("routes", result_data)
        self.assertIn("total_volume", result_data)

    def test_07_price_by_cmc_id_single(self):
        """Test new endpoint: /api/assets/price-by-cmc-id with single ID"""
        # Test with Bitcoin (CMC ID: 1)
        url = f"{BASE_URL}/api/assets/price-by-cmc-id?id=1"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed to fetch price by CMC ID: {response.text}")
        data = response.json()
        
        # Check response structure
        self.assertIn("data", data)
        self.assertIn("status", data)
        self.assertIn("timestamp", data["status"])
        self.assertIn("error_code", data["status"])
        self.assertEqual(data["status"]["error_code"], 0)
        
        # Check if data contains the requested ID
        if len(data["data"]) > 0:
            self.assertIn("1", data["data"])
            coin = data["data"]["1"]
            self.assertEqual(coin["cmc_id"], 1)
            self.assertIn("symbol", coin)
            self.assertIn("price", coin)
            self.assertIn("percent_change_24h", coin)

    def test_08_price_by_cmc_id_multiple(self):
        """Test new endpoint: /api/assets/price-by-cmc-id with multiple IDs"""
        # Test with BTC, ETH, BNB (1, 1027, 1839)
        url = f"{BASE_URL}/api/assets/price-by-cmc-id?id=1,1027,1839"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed to fetch multiple prices: {response.text}")
        data = response.json()
        
        self.assertIn("data", data)
        self.assertIn("status", data)
        self.assertEqual(data["status"]["error_code"], 0)
        
        # At least some IDs should be found
        self.assertIsInstance(data["data"], dict)

    def test_09_price_by_cmc_id_invalid(self):
        """Test new endpoint: /api/assets/price-by-cmc-id with invalid ID"""
        url = f"{BASE_URL}/api/assets/price-by-cmc-id?id=abc"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 400, "Should reject non-integer IDs")
        self.assertIn("Invalid CMC ID format", response.json()["detail"])

    def test_10_price_by_cmc_id_too_many(self):
        """Test new endpoint: /api/assets/price-by-cmc-id with too many IDs"""
        # Create 101 IDs
        ids = ",".join(str(i) for i in range(1, 102))
        url = f"{BASE_URL}/api/assets/price-by-cmc-id?id={ids}"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 400, "Should reject more than 100 IDs")
        self.assertIn("Too many IDs", response.json()["detail"])

    def test_11_price_by_cmc_id_missing(self):
        """Test new endpoint: /api/assets/price-by-cmc-id without ID parameter"""
        url = f"{BASE_URL}/api/assets/price-by-cmc-id"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 422, "Should reject missing ID parameter")

    def test_12_list_pools(self):
        """Test new endpoint: /api/pools"""
        url = f"{BASE_URL}/api/pools"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed to list pools: {response.text}")
        data = response.json()
        self.assertIsInstance(data, list)
        if len(data) > 0:
            self.assertIn("id", data[0])
            self.assertIn("pool_name", data[0])
            self.assertIn("tvl_usd", data[0])

    def test_13_pool_leaderboard(self):
        """Test new endpoint: /api/pools/{id}/leaderboard"""
        # First get a valid pool ID
        list_url = f"{BASE_URL}/api/pools"
        list_res = requests.get(list_url, auth=self.auth)
        pools = list_res.json()
        
        if len(pools) > 0:
            pool_id = pools[0]["id"]
            url = f"{BASE_URL}/api/pools/{pool_id}/leaderboard"
            response = requests.get(url, auth=self.auth)
            self.assertEqual(response.status_code, 200, f"Failed to fetch leaderboard: {response.text}")
            data = response.json()
            self.assertIsInstance(data, list)
            if len(data) > 0:
                self.assertIn("wallet_address", data[0])
                self.assertIn("share_percent", data[0])

    def test_14_pool_by_id(self):
        """Test /api/pool/{id} — fetch by numeric liquidity_pool.id (compound doc)."""
        # Use a known pool — WETH-USDC (id=1 is typically the first pool).
        url = f"{BASE_URL}/api/pool/1"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed: {response.text}")
        doc = response.json()
        data = doc["data"]
        self.assertEqual(data["type"], "pool")
        self.assertEqual(data["id"], 1)
        attrs = data.get("attributes", {})
        self.assertIn("pool_address", attrs)
        self.assertIn("links", attrs)
        self.assertIn("history", attrs)
        self.assertIn("protocol", attrs)
        rels = data.get("relationships", {})
        self.assertIn("coin0", rels)
        self.assertIn("coin1", rels)
        # default include = coin0,coin1
        self.assertIn("coin", {r["type"] for r in doc.get("included", [])})

    def test_15_pool_by_address(self):
        """Test /api/pool/{address} — fetch by V3 contract address."""
        # WETH-USDC 0.05% Uniswap V3 on Ethereum.
        addr = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
        url = f"{BASE_URL}/api/pool/{addr}"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed: {response.text}")
        data = response.json()["data"]
        attrs = data["attributes"]
        self.assertEqual(attrs["pool_address"].lower(), addr.lower())

        # Exact external link URLs.
        self.assertEqual(
            attrs["links"].get("uniswap"),
            "https://app.uniswap.org/explore/pools/ethereum/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        )
        self.assertEqual(
            attrs["links"].get("revert"),
            "https://revert.finance/#/pool/mainnet/uniswapv3/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        )
        self.assertEqual(
            attrs["links"].get("dexscreener"),
            "https://dexscreener.com/ethereum/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        )
        self.assertEqual(
            attrs["links"].get("defillama"),
            "https://defillama.com/yields/pool/665dc8bc-c79d-4800-97f7-304bf368e547",
        )

        # TVL sanity check: between 100k USD and 1B USD.
        tvl = attrs.get("tvl_usd")
        if tvl is not None:
            self.assertGreater(tvl, 100_000,
                               "TVL should be > $100k for a major pool")
            self.assertLess(tvl, 1_000_000_000,
                            "TVL should be < $1B")

    def test_16_pool_not_found(self):
        """Test /api/pool/{id} — 404 for non-existent id."""
        url = f"{BASE_URL}/api/pool/999999999"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 404,
                         f"Expected 404, got {response.status_code}: {response.text}")

    def test_17_pool_invalid_identifier(self):
        """Test /api/pool/{id} — 400 for bogus identifier format."""
        url = f"{BASE_URL}/api/pool/not-a-hex-or-number"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 400,
                         f"Expected 400, got {response.status_code}: {response.text}")

    def test_18_pool_links_present(self):
        """Test /api/pool/{id} — external links are populated for a real V3 pool."""
        # Arbitrum UNI-USDT 0.3% (pool_id 41338 from earlier investigation).
        url = f"{BASE_URL}/api/pool/41338"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed: {response.text}")
        attrs = response.json()["data"]["attributes"]
        links = attrs.get("links") or {}
        # A real Uniswap V3 pool should have at least a uniswap + dexscreener link.
        self.assertIn("uniswap", links, "Uniswap link missing")
        self.assertIn("dexscreener", links, "DexScreener link missing")
        for key, url_val in links.items():
            self.assertTrue(url_val.startswith("http"),
                            f"Link {key} is not a valid URL: {url_val}")

    def test_19_pool_history_present(self):
        """Test /api/pool/{id} — daily history array is present."""
        url = f"{BASE_URL}/api/pool/1"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed: {response.text}")
        history = response.json()["data"]["attributes"].get("history")
        self.assertIsInstance(history, list)
        if history:
            entry = history[0]
            self.assertIn("date", entry)
            self.assertIn("tvl_usd", entry)
            self.assertIn("volume_usd", entry)
            self.assertIn("tx_count", entry)


    def test_20_pool_links_tvl_validation(self):
        """For the top N pools by TVL with Uniswap links: verify link format matches pool type (v2/v3/v4) and
        check via Uniswap gateway API that the pool actually exists on-chain (best-effort)."""
        import concurrent.futures
        import json
        import re

        max_pools = int(os.getenv("POOL_MAX_CHECK", "100"))
        max_workers = int(os.getenv("POOL_UNISWAP_WORKERS", "5"))

        page_size = 500
        all_pool_ids = set()
        raw_pools = []
        for offset in range(0, max_pools, page_size):
            batch_size = min(page_size, max_pools - offset)
            list_url = f"{BASE_URL}/api/pools?limit={batch_size}&offset={offset}"
            resp = requests.get(list_url, auth=self.auth)
            self.assertEqual(resp.status_code, 200, f"Failed to list pools: {resp.text}")
            for p in resp.json():
                if p["id"] not in all_pool_ids:
                    all_pool_ids.add(p["id"])
                    raw_pools.append(p)

        targets = []
        url_failures = []
        for pool in raw_pools:
            proto = (pool.get("protocol") or "").lower()
            if "uniswap" not in proto and "v3" not in proto and "v2" not in proto:
                continue
            detail_doc = requests.get(f"{BASE_URL}/api/pool/{pool['id']}", auth=self.auth).json()
            detail = detail_doc.get("data", {})
            detail_attrs = detail.get("attributes", {})
            detail_rels = detail.get("relationships", {})
            uniswap_url = (detail_attrs.get("links") or {}).get("uniswap")
            if not uniswap_url:
                continue

            chain_lower = (detail_attrs.get("chain") or "").lower()
            if "arbitrum" in chain_lower:
                uniswap_chain = "ARBITRUM"
            elif "base" in chain_lower:
                uniswap_chain = "BASE"
            elif "optimism" in chain_lower:
                uniswap_chain = "OPTIMISM"
            elif "polygon" in chain_lower:
                uniswap_chain = "POLYGON"
            elif "bnb" in chain_lower or "bsc" in chain_lower:
                uniswap_chain = "BNB"
            else:
                uniswap_chain = "ETHEREUM"

            proto = (detail_attrs.get("protocol") or "").lower()
            is_v2 = "v2" in proto
            is_v4 = "v4" in proto
            ver = "v2" if is_v2 else ("v4" if is_v4 else "v3")

            link_addr = uniswap_url.rstrip("/").rsplit("/", 1)[-1]
            pool_addr = (detail_attrs.get("pool_address") or "").lower()

            def _pair_symbols():
                coins = detail_doc.get("included") or []
                by_id = {c["id"]: c for c in coins if c["type"] == "coin"}
                s0 = by_id.get((detail_rels.get("coin0") or {}).get("data", {}).get("id"), {}).get("attributes", {}).get("symbol")
                s1 = by_id.get((detail_rels.get("coin1") or {}).get("data", {}).get("id"), {}).get("attributes", {}).get("symbol")
                return f"{s0 or '?'}/{s1 or '?'}"

            if not is_v4 and pool_addr and link_addr.lower() != pool_addr:
                url_failures.append((detail["id"], _pair_symbols(),
                    uniswap_url, f"Link address {link_addr} differs from pool_address {pool_addr}"))
            pair = _pair_symbols()
            targets.append((detail["id"], pair, uniswap_url, uniswap_chain, ver, link_addr))

        self.assertGreater(len(targets), 0, "No Uniswap links found among top pools")

        gateway_failures = []
        gateway_unavailable = False
        gateway_url = "https://interface.gateway.uniswap.org/v1/graphql"

        for pid, name, url, chain, ver, link_addr in targets:
            parts = url.rstrip("/").split("/")
            if len(parts) < 6:
                url_failures.append((pid, name, url, "Malformed URL"))
                continue
            chain_seg = parts[-2]
            if chain_seg not in ("ethereum", "arbitrum", "base", "optimism", "polygon", "bnb"):
                url_failures.append((pid, name, url, f"Unknown chain segment: {chain_seg}"))
                continue

            if ver in ("v2", "v4"):
                if not re.match(r'^0x[a-fA-F0-9]{64}$', link_addr):
                    url_failures.append((pid, name, url, f"{ver.upper()} link must be 66-char hex, got: {link_addr}"))
            else:
                if not re.match(r'^0x[a-fA-F0-9]{40}$', link_addr):
                    url_failures.append((pid, name, url, f"V3 link must be 42-char hex, got: {link_addr}"))

        if url_failures:
            msg_parts = [f"  Pool {pid} ({name}): {reason} — {url}" for pid, name, url, reason in url_failures]
            self.fail(f"{len(url_failures)} Uniswap link(s) structurally invalid:\n" + "\n".join(msg_parts))

        def check_via_gateway(pid, name, url, chain, ver, link_addr):
            nonlocal gateway_unavailable
            if ver == "v2":
                return None
            pool_type = "v4Pool" if ver == "v4" else "v3Pool"
            query = json.dumps({
                "query": f"query {{ {pool_type}(chain: {chain}, address: \"{link_addr.lower()}\") {{ id }} }}",
            })
            try:
                resp = requests.post(gateway_url, data=query,
                    headers={"Content-Type": "application/json", "Origin": "https://app.uniswap.org"},
                    timeout=15)
                if resp.status_code == 409:
                    gateway_unavailable = True
                    return None
                if resp.status_code != 200:
                    return None
                pool_data = resp.json().get("data", {}).get(pool_type)
                if pool_data is None:
                    return (pid, name, url, f"Not found on Uniswap ({pool_type} on {chain})")
                return None
            except Exception:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(check_via_gateway, pid, name, url, ch, ver, la): pid
                       for pid, name, url, ch, ver, la in targets}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    gateway_failures.append(result)

        if gateway_unavailable and not gateway_failures:
            self.skipTest("Gateway API unavailable — structural checks passed")
        elif gateway_failures:
            msg_parts = [f"  Pool {pid} ({name}): {reason} — {url}" for pid, name, url, reason in gateway_failures]
            self.fail(f"{len(gateway_failures)} Uniswap link(s) not found on Uniswap:\n" + "\n".join(msg_parts))


    def test_21_pancakeswap_links_validation(self):
        """For the top N PancakeSwap pools: verify pancakeSwap link format matches pool type (v2/v3/v4)."""
        import re

        max_pools = int(os.getenv("PCS_MAX_CHECK", "100"))

        page_size = 500
        all_pool_ids = set()
        raw_pools = []

        lo, hi = 0, 50000
        while lo < hi:
            mid = (lo + hi + 1) // 2
            r = requests.get(f"{BASE_URL}/api/pools?limit=1&offset={mid}", auth=self.auth)
            if r.json():
                lo = mid
            else:
                hi = mid - 1
        total = lo + 1
        tail_offset = max(0, total - max(page_size, max_pools))

        for offset in range(tail_offset, total, page_size):
            batch_size = min(page_size, total - offset)
            list_url = f"{BASE_URL}/api/pools?limit={batch_size}&offset={offset}"
            resp = requests.get(list_url, auth=self.auth)
            self.assertEqual(resp.status_code, 200, f"Failed to list pools: {resp.text}")
            for p in resp.json():
                if "pancake" in (p.get("protocol") or "").lower() and p["id"] not in all_pool_ids:
                    all_pool_ids.add(p["id"])
                    raw_pools.append(p)
                if len(raw_pools) >= max_pools:
                    break
            if len(raw_pools) >= max_pools:
                break

        self.assertGreater(len(raw_pools), 0,
            "No PancakeSwap pools found — try increasing scan depth")

        targets = []
        for pool in raw_pools:
            d_doc = requests.get(f"{BASE_URL}/api/pool/{pool['id']}", auth=self.auth).json()
            d = d_doc.get("data", {})
            d_attrs = d.get("attributes", {})
            pcs_url = (d_attrs.get("links") or {}).get("pancakeswap")
            if not pcs_url:
                continue
            proto = (d_attrs.get("protocol") or "").lower()
            is_v4 = "v4" in proto
            coins = d_doc.get("included") or []
            by_id = {c["id"]: c for c in coins if c["type"] == "coin"}
            rels = d.get("relationships", {})
            s0 = by_id.get((rels.get("coin0") or {}).get("data", {}).get("id"), {}).get("attributes", {}).get("symbol")
            s1 = by_id.get((rels.get("coin1") or {}).get("data", {}).get("id"), {}).get("attributes", {}).get("symbol")
            pair = f"{s0 or '?'}/{s1 or '?'}"
            link_addr = pcs_url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
            targets.append((d["id"], pair, pcs_url, link_addr, is_v4))

        self.assertGreater(len(targets), 0,
            "No PancakeSwap links found — pools may lack pancakeSwap links")

        failures = []
        for pid, name, url, link_addr, is_v4 in targets:
            if not re.match(r'^0x[a-fA-F0-9]{40}$', link_addr) and \
               not re.match(r'^0x[a-fA-F0-9]{64}$', link_addr):
                failures.append((pid, name, url,
                    f"Link address must be 42 or 66-char hex, got: {link_addr}"))
            if "pancakeswap.finance" not in url:
                failures.append((pid, name, url, "URL does not point to pancakeswap.finance"))

        if failures:
            msg_parts = [
                f"  Pool {pid} ({name}): {reason} — {url}"
                for pid, name, url, reason in failures
            ]
            self.fail(f"{len(failures)} PancakeSwap link(s) invalid:\n" + "\n".join(msg_parts))


    def test_22_usd_pool_volume_validation(self):
        """Fetch USD-USD pools for last 8 days sorted by volume desc, validate links and TVL."""
        import re
        from datetime import datetime, timedelta, timezone

        limit = 50
        end_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")

        url = (
            f"{BASE_URL}/api/pools/search"
            f"?start_token=USD&end_token=USD"
            f"&start_date={start_date}&end_date={end_date}"
            f"&sort_by=volume&limit={limit}&stream=false"
        )
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Pool search failed: {resp.text}")

        data = resp.json()
        pools = data.get("pools", [])
        self.assertGreater(len(pools), 0, "No USD-USD pools found for the period")

        failures = []
        tvl_zero_pools = []
        for pool in pools:
            pid = pool["id"]
            pair = f"{pool['token0']['symbol']}/{pool['token1']['symbol']}"
            proto = (pool.get("protocol") or "").lower()
            links = pool.get("links") or {}
            tvl = pool.get("tvl_usd")

            if tvl is None or tvl <= 0:
                tvl_zero_pools.append((pid, pair, tvl))

            ds_url = links.get("dexscreener", "")
            if not ds_url:
                failures.append((pid, pair, "Missing DexScreener link"))
            elif "dexscreener.com" not in ds_url:
                failures.append((pid, pair, f"DexScreener URL invalid: {ds_url}"))

            has_uni = "uniswap" in links and links["uniswap"]
            has_pcs = "pancakeswap" in links and links["pancakeswap"]
            is_uniswap_proto = "uniswap" in proto or "v3" in proto or "v2" in proto
            is_pancake_proto = "pancake" in proto

            if is_uniswap_proto and not has_uni:
                failures.append((pid, pair, f"Missing Uniswap link for {proto}"))
            elif is_pancake_proto and not has_pcs:
                failures.append((pid, pair, f"Missing PancakeSwap link for {proto}"))

            if has_uni:
                uni_url = links["uniswap"]
                if "app.uniswap.org" not in uni_url:
                    failures.append((pid, pair, f"Uniswap URL domain invalid: {uni_url}"))
                else:
                    link_addr = uni_url.rstrip("/").rsplit("/", 1)[-1]
                    is_v4 = "v4" in proto
                    if is_v4:
                        if not re.match(r'^0x[a-fA-F0-9]{64}$', link_addr):
                            failures.append((pid, pair, f"V4 Uniswap link must be 66-char hex, got: {link_addr}"))
                    else:
                        if not re.match(r'^0x[a-fA-F0-9]{40}$', link_addr):
                            failures.append((pid, pair, f"Uniswap V3 link must be 42-char hex, got: {link_addr}"))

            if has_pcs:
                pcs_url = links["pancakeswap"]
                if "pancakeswap.finance" not in pcs_url:
                    failures.append((pid, pair, f"PancakeSwap URL domain invalid: {pcs_url}"))
                else:
                    link_addr = pcs_url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
                    if not re.match(r'^0x[a-fA-F0-9]{40}$', link_addr) and \
                       not re.match(r'^0x[a-fA-F0-9]{64}$', link_addr):
                        failures.append((pid, pair, f"PancakeSwap link address must be 42 or 66-char hex, got: {link_addr}"))

        msg_parts = []
        if failures:
            msg_parts.append(f"{len(failures)} link/format issue(s):")
            msg_parts.extend(f"  Pool {pid} ({pair}): {reason}" for pid, pair, reason in failures)
        if tvl_zero_pools:
            msg_parts.append(f"{len(tvl_zero_pools)} pool(s) with zero TVL:")
            msg_parts.extend(f"  Pool {pid} ({pair}): TVL={tvl}" for pid, pair, tvl in tvl_zero_pools)

        if msg_parts:
            self.fail("\n".join(msg_parts))

    def test_23_ods_search_by_contract(self):
        """Test /api/ods/search-by-contract with show_routes default (true)."""
        # WBNB -> USDT on BNB
        url = (
            f"{BASE_URL}/api/ods/search-by-contract"
            f"?origin_coin_contract_address=0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
            f"&destination_coin_contract_address=0x55d398326f99059ff775485246999027b3197955"
            f"&direction=forward"
        )
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"ODS search failed: {resp.text}")
        doc = resp.json()
        data = doc.get("data")
        self.assertIsNotNone(data, "Missing data block")
        items = data if isinstance(data, list) else [data]
        self.assertGreater(doc.get("meta", {}).get("n", 0), 0, "Expected at least one O&D pair")
        od = items[0]
        # O&D resources are JSON:API — type/id/attributes/relationships
        self.assertEqual(od["type"], "od")
        attrs = od["attributes"]
        self.assertEqual(attrs["chain"], "BNB")
        for key in ("chain_id", "origin_coin_contract_address",
                    "destination_coin_contract_address", "origin_coin_id", "dest_coin_id",
                    "origin_symbol", "dest_symbol", "first_seen", "last_seen"):
            self.assertIn(key, attrs, f"O&D missing field: {key}")
        self.assertIn("routes", od.get("relationships", {}))
        # show_routes default -> routes embedded in `included`
        included_types = {r["type"] for r in doc.get("included", [])}
        self.assertIn("route", included_types, "routes should be included by default")
        self.assertIn("hop", included_types, "route hops should be included")

    def test_24_ods_search_by_contract_show_routes_false(self):
        """Test /api/ods/search-by-contract with show_routes=false omits routes."""
        url = (
            f"{BASE_URL}/api/ods/search-by-contract"
            f"?origin_coin_contract_address=0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
            f"&destination_coin_contract_address=0x55d398326f99059ff775485246999027b3197955"
            f"&direction=forward&show_routes=false"
        )
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"ODS search failed: {resp.text}")
        doc = resp.json()
        self.assertGreater(doc.get("meta", {}).get("n", 0), 0)
        included_types = {r["type"] for r in doc.get("included", [])}
        self.assertNotIn("route", included_types, "routes should be omitted when show_routes=false")

    def test_25_ods_search_by_contract_no_match(self):
        """Test /api/ods/search-by-contract returns an empty list for an unknown pair."""
        url = (
            f"{BASE_URL}/api/ods/search-by-contract"
            f"?origin_coin_contract_address=0x000000000000000000000000000000000000dead"
            f"&destination_coin_contract_address=0x55d398326f99059ff775485246999027b3197955"
        )
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"ODS search failed: {resp.text}")
        doc = resp.json()
        self.assertEqual(doc.get("meta", {}).get("n", 0), 0)
        self.assertEqual(doc.get("data"), [])

    def test_26_od_by_hash(self):
        """Test /api/od/{od_hash} returns the full O&D pair as a compound document."""
        url = f"{BASE_URL}/api/od/2ac53c78a580597e"  # WBNB -> USDT on BNB
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"OD by hash failed: {resp.text}")
        doc = resp.json()
        od = doc["data"]
        self.assertIsNotNone(od, "Missing data block")
        self.assertEqual(od["type"], "od")
        self.assertEqual(od["id"], "2ac53c78a580597e")
        attrs = od["attributes"]
        self.assertEqual(attrs["chain"], "BNB")
        self.assertEqual(attrs["origin_symbol"], "WBNB")
        self.assertEqual(attrs["dest_symbol"], "USDT")
        for key in ("chain_id", "origin_coin_contract_address", "destination_coin_contract_address",
                    "origin_coin_id", "dest_coin_id", "first_seen", "last_seen"):
            self.assertIn(key, attrs, f"O&D missing field: {key}")
        # Default include = full drill-down
        included_types = {r["type"] for r in doc.get("included", [])}
        self.assertTrue({"route", "hop", "pool", "coin"}.issubset(included_types),
                        f"expected route/hop/pool/coin in included, got {included_types}")

    def test_27_od_by_hash_not_found(self):
        """Test /api/od/{od_hash} returns 404 for an unknown pair hash."""
        url = f"{BASE_URL}/api/od/0000000000000000"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 404, f"Expected 404 for unknown od_hash, got {resp.status_code}")

    def test_28_od_by_hash_invalid(self):
        """Test /api/od/{od_hash} returns 400 for a malformed hash."""
        url = f"{BASE_URL}/api/od/XYZ"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 400, f"Expected 400 for invalid od_hash, got {resp.status_code}")

    def test_29_coins_search_by_symbol(self):
        """Test public endpoint: /api/coins/search-by-symbol returns coin info + contracts."""
        url = f"{BASE_URL}/api/coins/search-by-symbol?symbol=WETH"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Coin search failed: {resp.text}")
        doc = resp.json()
        data = doc.get("data")
        self.assertIsNotNone(data, "Missing data block")
        items = data if isinstance(data, list) else [data]
        self.assertGreater(doc.get("meta", {}).get("n", 0), 0, "Expected at least one coin")
        coin = items[0]
        self.assertEqual(coin["type"], "coin")
        attrs = coin["attributes"]
        self.assertEqual(attrs["symbol"].upper(), "WETH")
        self.assertIn("name", attrs)
        self.assertIn("price", attrs)
        rels = coin.get("relationships", {})
        self.assertIn("contracts", rels)
        included = {r["type"] for r in doc.get("included", [])}
        self.assertIn("coin_contract", included, "Expected contracts in included")
        contracts = [r for r in doc.get("included", []) if r["type"] == "coin_contract"]
        self.assertGreater(len(contracts), 0, "Expected at least one contract")
        contract = contracts[0]["attributes"]
        for key in ("chain", "contract_address", "decimals", "is_native", "tracked"):
            self.assertIn(key, contract, f"Contract missing field: {key}")

    def test_30_coins_search_by_symbol_lowercase(self):
        """Test /api/coins/search-by-symbol is case-insensitive."""
        url = f"{BASE_URL}/api/coins/search-by-symbol?symbol=weth"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Coin search failed: {resp.text}")
        doc = resp.json()
        data = doc.get("data")
        items = data if isinstance(data, list) else [data]
        self.assertGreater(doc.get("meta", {}).get("n", 0), 0)
        self.assertEqual(items[0]["attributes"]["symbol"].upper(), "WETH")

    def test_31_coins_search_by_symbol_family_expansion(self):
        """Test /api/coins/search-by-symbol expands to the coin family by default."""
        url = f"{BASE_URL}/api/coins/search-by-symbol?symbol=BTC"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Coin search failed: {resp.text}")
        doc = resp.json()
        data = doc.get("data")
        items = data if isinstance(data, list) else [data]
        self.assertTrue(doc.get("meta", {}).get("include_coin_families"))
        symbols = {i["attributes"]["symbol"].upper() for i in items}
        self.assertIn("BTC", symbols)
        self.assertGreater(doc.get("meta", {}).get("n", 0), 1, "Expected family expansion to return wrapped BTC variants")

    def test_32_coins_search_by_symbol_no_family_expansion(self):
        """Test /api/coins/search-by-symbol with include_coin_families=false returns exact symbol only."""
        url = f"{BASE_URL}/api/coins/search-by-symbol?symbol=BTC&include_coin_families=false"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Coin search failed: {resp.text}")
        doc = resp.json()
        data = doc.get("data")
        items = data if isinstance(data, list) else [data]
        self.assertFalse(doc.get("meta", {}).get("include_coin_families"))
        self.assertEqual(doc.get("meta", {}).get("n", 0), 1)
        self.assertEqual(items[0]["attributes"]["symbol"].upper(), "BTC")

    def test_33_coins_search_by_symbol_not_found(self):
        """Test /api/coins/search-by-symbol returns 404 for an unknown symbol."""
        url = f"{BASE_URL}/api/coins/search-by-symbol?symbol=ZZZZNOTREAL"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 404, f"Expected 404 for unknown symbol, got {resp.status_code}")

    def test_34_od_list(self):
        """Test /api/ods list endpoint with filters + pagination."""
        url = f"{BASE_URL}/api/ods?origin_symbol=WBNB&limit=5"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"ODS list failed: {resp.text}")
        doc = resp.json()
        data = doc.get("data")
        items = data if isinstance(data, list) else [data]
        self.assertGreater(len(items), 0)
        self.assertEqual(items[0]["type"], "od")
        self.assertIn("attributes", items[0])

    def test_35_route_by_hash(self):
        """Test /api/routes/{route_hash} compound document."""
        url = f"{BASE_URL}/api/routes/837dc52fa8bde82c"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Route by hash failed: {resp.text}")
        doc = resp.json()
        route = doc["data"]
        self.assertEqual(route["type"], "route")
        self.assertEqual(route["id"], "837dc52fa8bde82c")
        included_types = {r["type"] for r in doc.get("included", [])}
        self.assertTrue({"hop", "pool", "coin"}.issubset(included_types),
                        f"expected hop/pool/coin in included, got {included_types}")

    def test_36_coin_by_id(self):
        """Test /api/coins/{coin_id} compound document."""
        url = f"{BASE_URL}/api/coins/290"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Coin by id failed: {resp.text}")
        doc = resp.json()
        coin = doc["data"]
        self.assertEqual(coin["type"], "coin")
        self.assertEqual(coin["id"], 290)
        self.assertEqual(coin["attributes"]["symbol"].upper(), "BTC")
        included_types = {r["type"] for r in doc.get("included", [])}
        self.assertTrue({"coin_contract", "coin_family"}.issubset(included_types),
                        f"expected contracts+families in included, got {included_types}")

    def test_37_bad_include_400(self):
        """Test bad ?include= path returns 400, not 500."""
        for url in (
            f"{BASE_URL}/api/od/2ac53c78a580597e?include=routes.hops.bogus",
            f"{BASE_URL}/api/coins/290?include=contracts.bogus",
            f"{BASE_URL}/api/pool/1?include=coin0.bogus",
        ):
            resp = requests.get(url, auth=self.auth)
            self.assertEqual(resp.status_code, 400, f"Expected 400 for {url}, got {resp.status_code}: {resp.text}")

    def test_38_route_daily_stats(self):
        """Test /api/routes/{route_hash}/daily-stats compound document."""
        url = f"{BASE_URL}/api/routes/837dc52fa8bde82c/daily-stats"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Route daily stats failed: {resp.text}")
        doc = resp.json()
        route = doc["data"]
        self.assertEqual(route["type"], "route")
        self.assertEqual(route["id"], "837dc52fa8bde82c")
        stats_refs = route["relationships"]["daily_stats"]["data"]
        self.assertGreater(len(stats_refs), 0, "Expected at least one daily stat row")
        included_types = {r["type"] for r in doc.get("included", [])}
        self.assertIn("route_daily_stat", included_types, "Expected route_daily_stat in included")
        stat = next(r for r in doc["included"] if r["type"] == "route_daily_stat")
        for key in ("day", "tx_count", "swap_count", "volume_usd", "fees_usd"):
            self.assertIn(key, stat["attributes"], f"route_daily_stat missing field: {key}")
        self.assertEqual(stat["relationships"]["route"]["data"]["id"], "837dc52fa8bde82c")
        # Default include also brings the bucket distribution.
        self.assertIn("route_daily_stat_bucket", included_types,
                      "Expected route_daily_stat_bucket in default include")
        bucket = next(r for r in doc["included"] if r["type"] == "route_daily_stat_bucket")
        for key in ("day", "bucket_index", "tx_count", "sample_count", "volume_usd",
                    "fees_usd", "log_sum", "log_sum2"):
            self.assertIn(key, bucket["attributes"], f"route_daily_stat_bucket missing field: {key}")
        # Each daily stat links to its same-day buckets.
        bucket_refs = stat["relationships"]["daily_stats_bucket"]["data"]
        if bucket_refs:
            for ref in bucket_refs:
                self.assertTrue(ref["id"].startswith(stat["id"] + ":"),
                                f"bucket {ref['id']} should be under daily stat {stat['id']}")

    def test_39_route_daily_stats_not_found(self):
        """Test /api/routes/{route_hash}/daily-stats 404 for unknown route."""
        url = f"{BASE_URL}/api/routes/0000000000000000/daily-stats"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 404, f"Expected 404, got {resp.status_code}: {resp.text}")

    def test_40_route_daily_stats_bad_include(self):
        """Test /api/routes/{route_hash}/daily-stats bad include returns 400."""
        url = f"{BASE_URL}/api/routes/837dc52fa8bde82c/daily-stats?include=daily_stats.bogus"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 400, f"Expected 400, got {resp.status_code}: {resp.text}")
        self.assertIn("bogus", resp.json().get("detail", ""))

    def test_41_pool_daily_stats(self):
        """Test /api/pool/{identifier}/daily-stats compound document."""
        url = f"{BASE_URL}/api/pool/165681/daily-stats"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Pool daily stats failed: {resp.text}")
        doc = resp.json()
        pool = doc["data"]
        self.assertEqual(pool["type"], "pool")
        self.assertEqual(pool["id"], 165681)
        stats_refs = pool["relationships"]["daily_stats"]["data"]
        self.assertGreater(len(stats_refs), 0, "Expected at least one daily stat row")
        included_types = {r["type"] for r in doc.get("included", [])}
        self.assertIn("pool_daily_stat", included_types, "Expected pool_daily_stat in included")
        stat = next(r for r in doc["included"] if r["type"] == "pool_daily_stat")
        for key in ("day", "tx_count", "volume_usd", "tvl_usd"):
            self.assertIn(key, stat["attributes"], f"pool_daily_stat missing field: {key}")
        self.assertEqual(stat["relationships"]["pool"]["data"]["id"], 165681)
        # Default include also brings the bucket distribution.
        self.assertIn("pool_daily_stat_bucket", included_types,
                      "Expected pool_daily_stat_bucket in default include")
        bucket = next(r for r in doc["included"] if r["type"] == "pool_daily_stat_bucket")
        for key in ("day", "bucket_index", "tx_count", "sample_count", "volume_usd",
                    "fees_usd", "log_sum", "log_sum2"):
            self.assertIn(key, bucket["attributes"], f"pool_daily_stat_bucket missing field: {key}")
        # Each daily stat links to its same-day buckets.
        bucket_refs = stat["relationships"]["daily_stats_bucket"]["data"]
        if bucket_refs:
            for ref in bucket_refs:
                self.assertTrue(ref["id"].startswith(stat["id"] + ":"),
                                f"bucket {ref['id']} should be under daily stat {stat['id']}")

    def test_42_pool_daily_stats_by_address(self):
        """Test /api/pool/{identifier}/daily-stats resolves a V3 contract address."""
        url = f"{BASE_URL}/api/pool/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640/daily-stats"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Pool daily stats by address failed: {resp.text}")
        doc = resp.json()
        self.assertEqual(doc["data"]["id"], 165681)
        self.assertGreater(len(doc["data"]["relationships"]["daily_stats"]["data"]), 0)

    def test_43_pool_daily_stats_errors(self):
        """Test pool daily-stats 404 (unknown id), 400 (bad identifier), 400 (bad include)."""
        urls_404 = (f"{BASE_URL}/api/pool/999999999/daily-stats",)
        for url in urls_404:
            resp = requests.get(url, auth=self.auth)
            self.assertEqual(resp.status_code, 404, f"Expected 404 for {url}, got {resp.status_code}: {resp.text}")
        url_bad_id = f"{BASE_URL}/api/pool/XYZ/daily-stats"
        resp = requests.get(url_bad_id, auth=self.auth)
        self.assertEqual(resp.status_code, 400, f"Expected 400 for {url_bad_id}, got {resp.status_code}: {resp.text}")
        url_bad_inc = f"{BASE_URL}/api/pool/165681/daily-stats?include=daily_stats.bogus"
        resp = requests.get(url_bad_inc, auth=self.auth)
        self.assertEqual(resp.status_code, 400, f"Expected 400 for {url_bad_inc}, got {resp.status_code}: {resp.text}")
        self.assertIn("bogus", resp.json().get("detail", ""))

    def test_44_route_window_stats(self):
        """Route daily-stats carries a window_stats attribute matching analyze."""
        url = f"{BASE_URL}/api/routes/837dc52fa8bde82c/daily-stats"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Route daily stats failed: {resp.text}")
        route = resp.json()["data"]
        ws = route["attributes"].get("window_stats")
        self.assertIsNotNone(ws, "Expected window_stats attribute on route")
        for key in ("start_date", "end_date", "tx_count", "swap_count", "volume_usd",
                    "fees_usd", "market_size", "avg_volume", "pct_volume", "last_activity"):
            self.assertIn(key, ws, f"route window_stats missing field: {key}")
        # window sums must equal the sum of the included per-day rows.
        daily = [r for r in resp.json().get("included", []) if r["type"] == "route_daily_stat"]
        self.assertGreater(len(daily), 0)
        self.assertEqual(ws["tx_count"], sum(d["attributes"]["tx_count"] for d in daily))
        self.assertEqual(ws["swap_count"], sum(d["attributes"]["swap_count"] for d in daily))
        self.assertAlmostEqual(ws["volume_usd"], sum(d["attributes"]["volume_usd"] for d in daily), places=2)
        self.assertEqual(ws["last_activity"], max(d["attributes"]["day"] for d in daily))

    def test_45_route_window_stats_explicit_dates(self):
        """?start_date/end_date narrows the window; ?days resolves a lookback."""
        url = f"{BASE_URL}/api/routes/837dc52fa8bde82c/daily-stats?start_date=2026-08-12&end_date=2026-08-13"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Route window stats failed: {resp.text}")
        ws = resp.json()["data"]["attributes"]["window_stats"]
        self.assertEqual(ws["start_date"], "2026-08-12")
        self.assertEqual(ws["end_date"], "2026-08-13")
        self.assertAlmostEqual(ws["volume_usd"], 1154.54, places=2)
        url_days = f"{BASE_URL}/api/routes/837dc52fa8bde82c/daily-stats?days=3"
        resp_days = requests.get(url_days, auth=self.auth)
        self.assertEqual(resp_days.status_code, 200, f"Route days window failed: {resp_days.text}")
        ws_days = resp_days.json()["data"]["attributes"]["window_stats"]
        self.assertEqual(ws_days["start_date"], "2026-08-12")
        self.assertEqual(ws_days["end_date"], "2026-08-15")

    def test_46_pool_window_stats_apr(self):
        """Pool daily-stats carries window_stats + apr matching analyze."""
        url = f"{BASE_URL}/api/pool/165681/daily-stats"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Pool daily stats failed: {resp.text}")
        pool = resp.json()["data"]
        attrs = pool["attributes"]
        ws = attrs.get("window_stats")
        self.assertIsNotNone(ws, "Expected window_stats attribute on pool")
        for key in ("start_date", "end_date", "tx_count", "volume_usd", "tvl_usd", "fees_usd"):
            self.assertIn(key, ws, f"pool window_stats missing field: {key}")
        daily = [r for r in resp.json().get("included", []) if r["type"] == "pool_daily_stat"]
        self.assertGreater(len(daily), 0)
        self.assertEqual(ws["tx_count"], sum(d["attributes"]["tx_count"] for d in daily))
        self.assertAlmostEqual(ws["volume_usd"], sum(d["attributes"]["volume_usd"] for d in daily), places=2)
        self.assertIn("apr", attrs, "Expected apr attribute on pool")
        # 0.05% WETH/USDC pool over its full history -> apr ~4.05% (0.0405)
        self.assertIsNotNone(attrs["apr"])
        self.assertAlmostEqual(attrs["apr"], 0.0405, places=4)

    def test_47_pool_window_stats_apr_dates(self):
        """?start_date/end_date on the pool window changes apr."""
        url = f"{BASE_URL}/api/pool/165681/daily-stats?start_date=2026-08-01&end_date=2026-08-15"
        resp = requests.get(url, auth=self.auth)
        self.assertEqual(resp.status_code, 200, f"Pool window stats failed: {resp.text}")
        pool = resp.json()["data"]["attributes"]
        self.assertEqual(pool["window_stats"]["start_date"], "2026-08-01")
        self.assertEqual(pool["window_stats"]["end_date"], "2026-08-15")
        self.assertAlmostEqual(pool["apr"], 0.0293, places=4)


if __name__ == "__main__":
    print(f"Starting API Tests against {BASE_URL}...")
    unittest.main()
