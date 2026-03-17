import os
import requests
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

CMC_API_KEY = os.getenv('CMC_API_KEY')
BASE_URL = "https://pro-api.coinmarketcap.com"

def is_valid_symbol(symbol: str) -> bool:
    """
    Check if a symbol is valid for CMC API.
    Filters out symbols with special characters that CMC doesn't support.
    """
    if not symbol:
        return False
    
    # Remove common invalid patterns
    if any(char in symbol for char in ['℉', '°', '™', '®', '©', '\n', '\t', ' ']):
        return False
    
    # Only allow alphanumeric and common crypto symbol characters
    allowed_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_')
    return all(c in allowed_chars for c in symbol.upper())

def fetch_crypto_prices(symbols: List[str], target_currency: str = 'USD') -> Dict[str, Dict]:
    """
    Fetch current prices and metrics for a list of symbols from CoinMarketCap.
    Batches requests to avoid URL length / symbol count limits.
    """
    if not symbols:
        return {}
    
    if not CMC_API_KEY:
        logging.error("CMC_API_KEY not found in environment.")
        return {}

    # Filter out invalid symbols
    original_symbols = symbols
    valid_symbols = [s for s in symbols if is_valid_symbol(s)]
    invalid_symbols = set(original_symbols) - set(valid_symbols)
    
    if invalid_symbols:
        logging.warning(f"Skipping {len(invalid_symbols)} invalid symbols: {sorted(list(invalid_symbols))[:10]}")
    
    if not valid_symbols:
        logging.warning("No valid symbols to fetch after filtering")
        return {}

    all_metrics = {}
    BATCH_SIZE = 50
    
    # Split into batches to avoid 400 Client Error (URI too long or too many symbols)
    for i in range(0, len(valid_symbols), BATCH_SIZE):
        batch = valid_symbols[i:i + BATCH_SIZE]
        symbol_list = ",".join([s.upper() for s in batch])
        
        logging.info(f"📡 Fetching price batch {i//BATCH_SIZE + 1} ({len(batch)} symbols)")
        
        url = f"{BASE_URL}/v2/cryptocurrency/quotes/latest"
        headers = {
            'X-CMC_PRO_API_KEY': CMC_API_KEY,
            'Accept': 'application/json'
        }
        params = {
            "symbol": symbol_list,
            "convert": target_currency,
            "skip_invalid": "true"
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            if data.get('status', {}).get('error_code') != 0:
                error_msg = data.get('status', {}).get('error_message', 'Unknown error')
                logging.error(f"CoinMarketCap API Error (Batch {i//BATCH_SIZE + 1}): {error_msg}")
                continue

            # Process this batch
            batch_data = data.get('data', {})
            for symbol in batch:
                sym_upper = symbol.upper()
                if sym_upper in batch_data:
                    entries = batch_data[sym_upper]
                    entry = entries[0] if isinstance(entries, list) else entries
                    
                    if entry and 'quote' in entry:
                        quote = entry['quote'].get(target_currency, {})
                        all_metrics[symbol] = {
                            'price': quote.get('price'),
                            'percent_change_1h': quote.get('percent_change_1h'),
                            'percent_change_24h': quote.get('percent_change_24h'),
                            'percent_change_7d': quote.get('percent_change_7d'),
                            'percent_change_30d': quote.get('percent_change_30d'),
                            'percent_change_60d': quote.get('percent_change_60d'),
                            'percent_change_90d': quote.get('percent_change_90d'),
                            'market_cap': quote.get('market_cap'),
                            'market_cap_dominance': quote.get('market_cap_dominance'),
                            'fully_diluted_market_cap': quote.get('fully_diluted_market_cap'),
                            'tvl': quote.get('tvl'),
                            'last_updated': quote.get('last_updated'),
                            'total_supply': entry.get('total_supply'),
                            'circulating_supply': entry.get('circulating_supply'),
                            'max_supply': entry.get('max_supply'),
                        }
        except Exception as e:
            logging.error(f"Failed to fetch price batch {i//BATCH_SIZE + 1}: {e}")

    return all_metrics

def fetch_crypto_quotes_by_id(cmc_ids: List[int], target_currency: str = 'USD') -> Dict[int, Dict]:
    """
    Fetch current prices and metrics for a list of CMC IDs.
    This is preferred over symbols as it is unambiguous and more robust.
    Returns a dict mapping cmc_id (int) -> metrics dict.
    """
    if not cmc_ids:
        return {}
    
    if not CMC_API_KEY:
        logging.error("CMC_API_KEY not found in environment.")
        return {}

    all_metrics = {}
    BATCH_SIZE = 100 # IDs are safer than symbols, we can use larger batches
    
    # CMC IDs are just integers, no encoding issues
    for i in range(0, len(cmc_ids), BATCH_SIZE):
        batch = cmc_ids[i:i + BATCH_SIZE]
        id_list = ",".join([str(cid) for cid in batch])
        
        logging.info(f"📡 Fetching price batch {i//BATCH_SIZE + 1} ({len(batch)} IDs)")
        
        url = f"{BASE_URL}/v2/cryptocurrency/quotes/latest"
        headers = {
            'X-CMC_PRO_API_KEY': CMC_API_KEY,
            'Accept': 'application/json'
        }
        params = {
            "id": id_list,
            "convert": target_currency,
            "skip_invalid": "true"
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            if response.status_code != 200:
                logging.error(f"CMC API Error {response.status_code}: {response.text}")
                continue
                
            data = response.json()
            if data.get('status', {}).get('error_code') != 0:
                logging.error(f"CMC API Logic Error: {data.get('status', {}).get('error_message')}")
                continue

            # Quotes are indexed by ID string in the 'data' object
            batch_data = data.get('data', {})
            for cid in batch:
                entry = batch_data.get(str(cid))
                if entry and 'quote' in entry:
                    quote = entry['quote'].get(target_currency, {})
                    all_metrics[cid] = {
                        'price': quote.get('price'),
                        'percent_change_1h': quote.get('percent_change_1h'),
                        'percent_change_24h': quote.get('percent_change_24h'),
                        'percent_change_7d': quote.get('percent_change_7d'),
                        'percent_change_30d': quote.get('percent_change_30d'),
                        'percent_change_60d': quote.get('percent_change_60d'),
                        'percent_change_90d': quote.get('percent_change_90d'),
                        'market_cap': quote.get('market_cap'),
                        'market_cap_dominance': quote.get('market_cap_dominance'),
                        'fully_diluted_market_cap': quote.get('fully_diluted_market_cap'),
                        'tvl': quote.get('tvl'),
                        'last_updated': quote.get('last_updated'),
                        'total_supply': entry.get('total_supply'),
                        'circulating_supply': entry.get('circulating_supply'),
                        'max_supply': entry.get('max_supply'),
                    }
        except Exception as e:
            logging.error(f"Unexpected error in ID price batch {i//BATCH_SIZE + 1}: {e}")

    return all_metrics

def fetch_crypto_history(symbol: str, target_currency: str = 'USD', days: int = 730) -> List[Dict]:
    """
    Fetch historical prices for a symbol from CoinMarketCap.
    
    Note: Historical data requires CMC Pro plan. This function will attempt to fetch
    but may return empty if the plan doesn't support it.
    
    Returns a list of dicts with timestamp and price.
    """
    if not CMC_API_KEY:
        logging.error("CMC_API_KEY not found in environment.")
        return []

    url = f"{BASE_URL}/v3/cryptocurrency/quotes/historical"
    headers = {
        'X-CMC_PRO_API_KEY': CMC_API_KEY,
        'Accept': 'application/json'
    }
    
    # Calculate date range
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)
    
    params = {
        "symbol": symbol.upper(),
        "convert": target_currency,
        "time_start": int(start_time.timestamp()),
        "time_end": int(end_time.timestamp()),
        "interval": "daily",
        "count": days
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        if data.get('status', {}).get('error_code') != 0:
            error_msg = data.get('status', {}).get('error_message', 'Unknown error')
            logging.warning(f"CoinMarketCap Historical API Error for {symbol}: {error_msg}")
            logging.warning("Historical data may require CMC Pro plan. Skipping historical backfill.")
            return []

        history = []
        # Parse the quotes from the response
        quotes = data.get('data', {}).get('quotes', [])
        for quote in quotes:
            timestamp = quote.get('timestamp')
            price_data = quote.get('quote', {}).get(target_currency, {})
            price = price_data.get('close') or price_data.get('price')
            
            if timestamp and price:
                # Convert ISO timestamp to Unix timestamp
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                history.append({
                    'timestamp': int(dt.timestamp()),
                    'price': float(price)
                })
        
        return history

    except Exception as e:
        logging.warning(f"Failed to fetch history for {symbol} from CoinMarketCap: {e}")
        logging.warning("This is expected if using CMC Basic plan (historical data requires Pro).")
        return []

if __name__ == "__main__":
    # Test
    test_symbols = ["BTC", "ETH", "USDC"]
    print("Testing CoinMarketCap client...")
    prices = fetch_crypto_prices(test_symbols)
    print(f"Current prices: {prices}")
    
    if prices:
        print("\nTesting historical data (may fail on Basic plan)...")
        history = fetch_crypto_history("BTC", days=7)
        print(f"BTC history points: {len(history)}")
