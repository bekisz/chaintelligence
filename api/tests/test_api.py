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
        """Test public endpoint: /api/coin/list"""
        url = f"{BASE_URL}/api/coin/list"
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
        """Test /api/pool/{id} — fetch by numeric liquidity_pool.id."""
        # Use a known pool — WETH-USDC (id=1 is typically the first pool).
        url = f"{BASE_URL}/api/pool/1"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed: {response.text}")
        data = response.json()
        self.assertEqual(data["id"], 1)
        self.assertIn("pool_address", data)
        self.assertIn("links", data)
        self.assertIn("history", data)
        self.assertIn("token0", data)
        self.assertIn("token1", data)
        self.assertIn("chain", data)
        self.assertIn("protocol", data)

    def test_15_pool_by_address(self):
        """Test /api/pool/{address} — fetch by V3 contract address."""
        # WETH-USDC 0.05% Uniswap V3 on Ethereum.
        addr = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
        url = f"{BASE_URL}/api/pool/{addr}"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed: {response.text}")
        data = response.json()
        self.assertEqual(data["pool_address"].lower(), addr.lower())

        # Exact external link URLs.
        self.assertEqual(
            data["links"].get("uniswap"),
            "https://app.uniswap.org/explore/pools/ethereum/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        )
        self.assertEqual(
            data["links"].get("revert"),
            "https://revert.finance/#/pool/mainnet/uniswapv3/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        )
        self.assertEqual(
            data["links"].get("dexscreener"),
            "https://dexscreener.com/ethereum/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        )
        self.assertEqual(
            data["links"].get("defillama"),
            "https://defillama.com/yields/pool/665dc8bc-c79d-4800-97f7-304bf368e547",
        )

        # TVL sanity check: between 100k USD and 1B USD.
        if data.get("tvl_usd") is not None:
            self.assertGreater(data["tvl_usd"], 100_000,
                               "TVL should be > $100k for a major pool")
            self.assertLess(data["tvl_usd"], 1_000_000_000,
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
        # Arbitrum UNI-USDT 0.3% (pool_id 41294 from earlier investigation).
        url = f"{BASE_URL}/api/pool/41338"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed: {response.text}")
        data = response.json()
        self.assertIn("links", data)
        links = data["links"]
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
        data = response.json()
        self.assertIsInstance(data["history"], list)
        if data["history"]:
            entry = data["history"][0]
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
            detail = requests.get(f"{BASE_URL}/api/pool/{pool['id']}", auth=self.auth).json()
            uniswap_url = detail.get("links", {}).get("uniswap")
            if not uniswap_url:
                continue

            chain_lower = detail.get("chain", "").lower()
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

            proto = detail.get("protocol", "").lower()
            is_v2 = "v2" in proto
            is_v4 = "v4" in proto
            ver = "v2" if is_v2 else ("v4" if is_v4 else "v3")

            link_addr = uniswap_url.rstrip("/").rsplit("/", 1)[-1]
            pool_addr = detail.get("pool_address", "").lower()
            if not is_v4 and pool_addr and link_addr.lower() != pool_addr:
                url_failures.append((detail["id"], f"{detail['token0']['symbol']}/{detail['token1']['symbol']}",
                    uniswap_url, f"Link address {link_addr} differs from pool_address {pool_addr}"))
            pair = f"{detail['token0']['symbol']}/{detail['token1']['symbol']}"
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
            d = requests.get(f"{BASE_URL}/api/pool/{pool['id']}", auth=self.auth).json()
            pcs_url = d.get("links", {}).get("pancakeswap")
            if not pcs_url:
                continue
            proto = d.get("protocol", "").lower()
            is_v4 = "v4" in proto
            pair = f"{d['token0']['symbol']}/{d['token1']['symbol']}"
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


if __name__ == "__main__":
    print(f"Starting API Tests against {BASE_URL}...")
    unittest.main()
