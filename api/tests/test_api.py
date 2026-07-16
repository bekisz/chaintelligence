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

    def test_01_coin_list(self):
        """Test public endpoint: /api/coin/list"""
        url = f"{BASE_URL}/api/coin/list"
        response = requests.get(url)
        self.assertEqual(response.status_code, 200, f"Failed to fetch coin list: {response.text}")
        data = response.json()
        self.assertIsInstance(data, list)
        if len(data) > 0:
            self.assertIn("symbol", data[0])
            self.assertIn("name", data[0])

    def test_02_price_history(self):
        """Test public endpoint: /api/coin/price-history"""
        # Testing with ETH which is likely to exist
        url = f"{BASE_URL}/api/coin/price-history?symbol=ETH"
        response = requests.get(url)
        self.assertEqual(response.status_code, 200, f"Failed to fetch price history: {response.text}")
        data = response.json()
        self.assertEqual(data["symbol"], "ETH")
        self.assertIn("data", data)
        self.assertIsInstance(data["data"], list)

    def test_03_date_range(self):
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

    def test_04_lp_summary(self):
        """Test protected endpoint: /api/lp/position-summary"""
        url = f"{BASE_URL}/api/lp/position-summary"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Failed to fetch LP summary: {response.text}")
        data = response.json()
        self.assertIsInstance(data, list)

    def test_05_analyze_routes(self):
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

    def test_06_price_by_cmc_id_single(self):
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

    def test_07_price_by_cmc_id_multiple(self):
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

    def test_08_price_by_cmc_id_invalid(self):
        """Test new endpoint: /api/assets/price-by-cmc-id with invalid ID"""
        url = f"{BASE_URL}/api/assets/price-by-cmc-id?id=abc"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 400, "Should reject non-integer IDs")
        self.assertIn("Invalid CMC ID format", response.json()["detail"])

    def test_09_price_by_cmc_id_too_many(self):
        """Test new endpoint: /api/assets/price-by-cmc-id with too many IDs"""
        # Create 101 IDs
        ids = ",".join(str(i) for i in range(1, 102))
        url = f"{BASE_URL}/api/assets/price-by-cmc-id?id={ids}"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 400, "Should reject more than 100 IDs")
        self.assertIn("Too many IDs", response.json()["detail"])

    def test_10_price_by_cmc_id_missing(self):
        """Test new endpoint: /api/assets/price-by-cmc-id without ID parameter"""
        url = f"{BASE_URL}/api/assets/price-by-cmc-id"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 422, "Should reject missing ID parameter")

    def test_11_list_pools(self):
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

    def test_12_pool_leaderboard(self):
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

if __name__ == "__main__":
    print(f"Starting API Tests against {BASE_URL}...")
    unittest.main()
