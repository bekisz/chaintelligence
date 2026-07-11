import os
import requests
import logging
from typing import Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

CRYPTOCOMPARE_API_KEY = os.getenv('CRYPTOCOMPARE_API_KEY')
BASE_URL = "https://min-api.cryptocompare.com/data/pricemultifull"

# Symbol mapping for CryptoCompare
PRICE_SYMBOL_MAPPING = {
    'WETH': 'ETH',
    'WBTC': 'BTC',
    'WSTETH': 'ETH',
    'RETH': 'ETH',
    'CBETH': 'ETH',
    'SAVINGS USDS': 'USDS',
    'SUSDS': 'USDS',
}

def fetch_crypto_prices(symbols: List[str], target_currency: str = 'USD') -> Dict[str, float]:
    """
    Fetch current prices for a list of symbols from CryptoCompare.
    Returns a dict mapping symbol -> price.
    """
    if not symbols:
        return {}
    
    if not CRYPTOCOMPARE_API_KEY:
        logging.error("CRYPTOCOMPARE_API_KEY not found in environment.")
        return {}

    # CryptoCompare uses commas for multiple symbols
    fsyms = ",".join(symbols)
    params = {
        "fsyms": fsyms,
        "tsyms": target_currency,
        "api_key": CRYPTOCOMPARE_API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get('Response') == 'Error':
            logging.error(f"CryptoCompare API Error: {data.get('Message')}")
            return {}

        prices = {}
        raw_data = data.get('RAW', {})
        for sym in symbols:
            # CryptoCompare might return data in uppercase even if input was lowercase
            sym_upper = sym.upper()
            if sym_upper in raw_data:
                price = raw_data[sym_upper].get(target_currency, {}).get('PRICE')
                if price is not None:
                    prices[sym] = float(price)
        
        return prices

    except Exception as e:
        logging.error(f"Failed to fetch prices from CryptoCompare: {e}")
        return {}

def fetch_crypto_history(symbol: str, target_currency: str = 'USD', limit: int = 2000, all_data: bool = False) -> List[Dict]:
    """
    Fetch daily historical prices for a symbol from CryptoCompare.
    Returns a list of dicts with timestamp and price.
    """
    url = "https://min-api.cryptocompare.com/data/v2/histoday"
    params = {
        "fsym": symbol.upper(),
        "tsym": target_currency,
        "limit": limit,
        "allData": "true" if all_data else "false",
        "api_key": CRYPTOCOMPARE_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get('Response') == 'Error':
            logging.error(f"CryptoCompare API Error for {symbol}: {data.get('Message')}")
            return []

        history = []
        for d in data.get('Data', {}).get('Data', []):
            history.append({
                'timestamp': d['time'], # Unix timestamp
                'price': float(d['close'])
            })
        
        return history

    except Exception as e:
        logging.error(f"Failed to fetch history for {symbol} from CryptoCompare: {e}")
        return []

if __name__ == "__main__":
    # Test
    test_symbols = ["BTC", "ETH", "USDC"]
    print(fetch_crypto_prices(test_symbols))
