import os
import sys
import psycopg2
import logging
from datetime import datetime

# Add parent dirs
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../'))
sys.path.append(os.path.join(ROOT_DIR, 'chain-feeder'))
sys.path.append(os.path.join(ROOT_DIR, 'chain-feeder/include'))

from include.defillama_client import fetch_historical_prices

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# DB Config
DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def test_defillama():
    logger.info("Testing DeFi Llama client...")
    usdc_addr = "0xa0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    history = fetch_historical_prices(usdc_addr)
    if history:
        logger.info(f"✅ Successfully fetched {len(history)} points for USDC.")
        return True
    else:
        logger.error("❌ Failed to fetch history from DeFi Llama.")
        return False

def test_db_insert():
    logger.info("Testing DB insertion...")
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        
        # Mock data (lowercase to match DB)
        test_address = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48" # USDC
        test_symbol = "USDC"
        test_ts = datetime(2020, 1, 1)
        test_price = 1.0001
        
        cur.execute("""
            INSERT INTO coin_price_history (address, symbol, timestamp, price)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (address, timestamp) DO UPDATE SET price = EXCLUDED.price
        """, (test_address, test_symbol, test_ts, test_price))
        
        conn.commit()
        logger.info("✅ Successfully inserted/updated test record in DB.")
        
        # Verify
        cur.execute("SELECT price FROM coin_price_history WHERE address = %s AND timestamp = %s", (test_address, test_ts))
        row = cur.fetchone()
        if row and float(row[0]) == test_price:
            logger.info("✅ Verification successful.")
        else:
            logger.error("❌ Verification failed.")
            
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ DB Error: {e}")
        return False

if __name__ == "__main__":
    dl_ok = test_defillama()
    db_ok = test_db_insert()
    
    if dl_ok and db_ok:
        logger.info("\n🎉 All tests passed!")
    else:
        logger.error("\nSome tests failed.")
        sys.exit(1)
