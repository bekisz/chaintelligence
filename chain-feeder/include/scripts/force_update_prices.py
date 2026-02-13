import os
import sys
import psycopg2
import logging
from datetime import datetime

# Add parent dir
sys.path.append(os.path.join(os.path.dirname(__file__), '../'))
from cryptocompare_client import fetch_crypto_prices

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# DB Config
DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=postgres port=5432")

def run():
    logger.info("Starting manual price update...")
    
    # 1. Get Symbols to Update
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        
        # Focus on key assets for now to be fast
        symbols_to_update = ['ETH', 'WETH', 'USDC', 'USDT', 'DAI', 'WBTC', 'AAVE', 'LINK', 'UNI']
        
        # Fetch current prices
        logger.info(f"Fetching prices for: {symbols_to_update}")
        prices = fetch_crypto_prices(symbols_to_update)
        
        if not prices:
             logger.error("No prices fetched!")
             return

        logger.info(f"Fetched: {prices}")
        
        # Update DB
        updated = 0
        now = datetime.now()
        
        for sym, price in prices.items():
            try:
                # Update both exact symbol and Upper
                cur.execute("""
                    UPDATE coin 
                    SET price = %s, price_timestamp = %s
                    WHERE UPPER(symbol) = %s
                """, (price, now, sym.upper()))
                updated += cur.rowcount
            except Exception as e:
                logger.error(f"Error updating {sym}: {e}")
                conn.rollback()
        
        conn.commit()
        logger.info(f"Updated {updated} coin rows.")
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"DB Error: {e}")

if __name__ == "__main__":
    run()
