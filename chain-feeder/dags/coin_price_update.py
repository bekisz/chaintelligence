from airflow import DAG
from airflow.sdk import task, Asset
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
from datetime import timedelta
import logging

# Import the new client
from include.cryptocompare_client import fetch_crypto_prices, fetch_crypto_history

# Assets
asset_coin_prices = Asset("postgres://postgres:5432/chaintelligence/public/coin")
asset_coin_history = Asset("postgres://postgres:5432/chaintelligence/public/coin_price_history")

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=2),
}

# Symbol mapping for CryptoCompare
# Some tokens are 'wrapped' or have variations that might not match CryptoCompare's primary symbols
SYMBOL_MAPPING = {
    'WETH': 'ETH',
    'WBTC': 'BTC',
    'WSTETH': 'ETH', # Approximate or could use stETH if available
    'RETH': 'ETH',
    'CBETH': 'ETH',
    'SAVINGS USDS': 'USDS',
    'SUSDS': 'USDS',
}

@task(outlets=[asset_coin_prices])
def update_coin_prices():
    """
    Fetch all symbols from the 'coin' table and update their current price using CryptoCompare.
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    # 1. Get all symbols from coin table
    query = "SELECT symbol FROM coin"
    rows = pg_hook.get_records(query)
    if not rows:
        logging.info("No coins found in 'coin' table.")
        return
    
    original_symbols = [row[0] for row in rows]
    
    # 2. Map symbols for CryptoCompare
    # We'll fetch for both mapped and original, then use mapped results for originals
    fetch_symbols_set = set()
    for sym in original_symbols:
        fetch_sym = SYMBOL_MAPPING.get(sym.upper(), sym.upper())
        fetch_symbols_set.add(fetch_sym)
    
    fetch_symbols = list(fetch_symbols_set)
    logging.info(f"Fetching prices for {len(fetch_symbols)} distinct symbols from CryptoCompare.")
    
    # 3. Fetch prices
    all_prices = fetch_crypto_prices(fetch_symbols)
    
    if not all_prices:
        logging.error("Failed to fetch any prices from CryptoCompare.")
        return

    # 4. Update the coin table
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    updated_count = 0
    now = pendulum.now()
    
    for sym in original_symbols:
        fetch_sym = SYMBOL_MAPPING.get(sym.upper(), sym.upper())
        price = all_prices.get(fetch_sym)
        
        if price is not None:
            try:
                cur.execute("""
                    UPDATE coin 
                    SET price = %s, price_timestamp = %s
                    WHERE symbol = %s
                """, (price, now, sym))
                updated_count += 1
            except Exception as e:
                logging.error(f"Failed to update price for {sym}: {e}")
                conn.rollback()
        else:
            logging.warning(f"No price found for symbol {sym} (mapped to {fetch_sym})")
            
    conn.commit()
    cur.close()
    conn.close()
    
    logging.info(f"Successfully updated {updated_count} coin prices.")

    # 5. History Backfill and Maintenance
    # Check for coins with no history
    missing_history = pg_hook.get_records("""
        SELECT c.symbol 
        FROM coin c
        LEFT JOIN coin_price_history h ON c.symbol = h.symbol
        GROUP BY c.symbol
        HAVING COUNT(h.timestamp) = 0
    """)
    
    if missing_history:
        logging.info(f"Coins missing history: {[r[0] for r in missing_history]}")
        for row in missing_history:
            sym = row[0]
            fetch_sym = SYMBOL_MAPPING.get(sym.upper(), sym.upper())
            history = fetch_crypto_history(fetch_sym)
            if history:
                logging.info(f"Backfilling {len(history)} data points for {sym}")
                conn = pg_hook.get_conn()
                cur = conn.cursor()
                try:
                    data = [(sym, pendulum.from_timestamp(h['timestamp']), h['price']) for h in history]
                    cur.executemany("""
                        INSERT INTO coin_price_history (symbol, timestamp, price)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (symbol, timestamp) DO UPDATE SET price = EXCLUDED.price
                    """, data)
                    conn.commit()
                except Exception as e:
                    logging.error(f"Failed to backfill history for {sym}: {e}")
                    conn.rollback()
                finally:
                    cur.close()
                    conn.close()

    # 6. Daily snapshot: Ensure current price is in history for TODAY
    # We use a rounded timestamp (start of day) for consistency with histoday
    today = pendulum.now().start_of('day')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO coin_price_history (symbol, timestamp, price)
            SELECT symbol, %s, price FROM coin
            WHERE price IS NOT NULL
            ON CONFLICT (symbol, timestamp) DO UPDATE SET price = EXCLUDED.price
        """, (today,))
        conn.commit()
    except Exception as e:
        logging.error(f"Failed to record daily snapshots: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

    return f"Updated {updated_count} coins and history."

with DAG(
    'coin_price_update',
    default_args=default_args,
    description='Update coin prices every 10 minutes using CryptoCompare',
    schedule='*/10 * * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['prices', 'cryptocompare', 'maintenance'],
) as dag:

    update_task = update_coin_prices()
