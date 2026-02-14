import requests
import logging
from typing import Dict, List, Optional
from datetime import datetime

_logger = logging.getLogger(__name__)

BASE_URL = "https://coins.llama.fi"

def fetch_historical_prices(address: str, chain: str = "ethereum", start_timestamp: Optional[int] = None, end_timestamp: Optional[int] = None, points: int = 1000) -> List[Dict]:
    """
    Fetch historical prices from DeFi Llama for a specific token address.
    
    The 'span' parameter determines the number of daily points to retrieve.
    By default we fetch up to 1000 points (~2.7 years).
    """
    coin_key = f"{chain}:{address.lower()}"
    url = f"{BASE_URL}/chart/{coin_key}"
    
    params = {
        "span": points
    }
    if start_timestamp:
        params["start"] = start_timestamp
    if end_timestamp:
        params["end"] = end_timestamp

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        coin_data = data.get("coins", {}).get(coin_key, {})
        prices = coin_data.get("prices", [])
        
        if not prices:
            _logger.warning(f"No historical prices found for {coin_key}")
            return []

        return [
            {
                "timestamp": p["timestamp"],
                "price": float(p["price"])
            }
            for p in prices
        ]

    except Exception as e:
        _logger.error(f"Failed to fetch history from DeFi Llama for {coin_key}: {e}")
        return []

if __name__ == "__main__":
    # Test with USDC
    usdc_addr = "0xa0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    history = fetch_historical_prices(usdc_addr)
    print(f"Fetched {len(history)} price points for USDC")
    if history:
        print(f"First: {history[0]}")
        print(f"Last: {history[-1]}")
