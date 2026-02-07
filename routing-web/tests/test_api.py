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
        # Test a common pair
        url = f"{BASE_URL}/api/routes/analyze?start_token=ETH&end_token=USDC&days=1"
        response = requests.get(url, auth=self.auth)
        self.assertEqual(response.status_code, 200, f"Analysis failed: {response.text}")
        data = response.json()
        self.assertIn("routes", data)
        self.assertIn("total_volume", data)

if __name__ == "__main__":
    print(f"Starting API Tests against {BASE_URL}...")
    unittest.main()
