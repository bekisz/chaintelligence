import requests
import os
import unittest
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BASE_URL = os.getenv("API_URL", "http://localhost:8000")
USERNAME = os.getenv("PORTAL_USERNAME", "admin")
PASSWORD = os.getenv("PORTAL_PASSWORD", "chaintelligence")

class TestDagTriggerAPI(unittest.TestCase):
    def setUp(self):
        self.auth = (USERNAME, PASSWORD)
        self.url = f"{BASE_URL}/api/coin/dag/coin-history-feeder"

    def test_trigger_unauthorized(self):
        """Test that the endpoint is protected."""
        response = requests.post(self.url, json={"force_update": False})
        self.assertEqual(response.status_code, 401)

    def test_trigger_bad_request(self):
        """Test with invalid payload type."""
        response = requests.post(self.url, json={"force_update": "not_a_bool"}, auth=self.auth)
        self.assertEqual(response.status_code, 422)

    def test_trigger_valid_payload(self):
        """
        Test with valid payload. 
        Note: This will actually attempt to trigger the DAG if Airflow is up.
        We expect 200 if triggered, or 502/503 if Airflow is not reachable.
        """
        payload = {
            "force_update": False,
            "coin_symbols": ["ETH"]
        }
        response = requests.post(self.url, json=payload, auth=self.auth)
        
        # We accept either a success trigger or a connection error to Airflow (if running locally without full docker stack)
        # But in the user's setup, if the server is running, Airflow should be reachable.
        self.assertIn(response.status_code, [200, 502, 503], f"Unexpected status code: {response.status_code} - {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            self.assertIn("Successfully triggered", data["message"])
            self.assertIn("dag_run_id", data)

if __name__ == "__main__":
    unittest.main()
