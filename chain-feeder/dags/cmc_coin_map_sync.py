from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
import requests
import logging

# Configuration
CMC_API_KEY = "ee501995-c447-4274-96a9-cbb7bf06e6bc"
CMC_MAP_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"

default_args = {
    'owner': 'airflow',
    'retries': 1,
    'retry_delay': pendulum.duration(minutes=5),
}

with DAG(
    'cmc_coin_map_sync',
    default_args=default_args,
    description='Sync CoinMarketCap ID map to coin table daily',
    schedule='@daily',
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=['metadata', 'cmc'],
) as dag:

    @task
    def sync_cmc_map():
        headers = {
            'X-CMC_PRO_API_KEY': CMC_API_KEY,
            'Accept': 'application/json'
        }
        
        logging.info(f"Fetching CMC map from {CMC_MAP_URL}")
        response = requests.get(CMC_MAP_URL, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        coins = data.get('data', [])
        logging.info(f"Received {len(coins)} coins from CMC")

        pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        
        # Filters:
        # 1. is_active == 1
        # 2. rank <= 1000 (CMC gives rank as integer)
        # 3. platform is ethereum (for tokens) or platform is None (for native BTC/ETH)
        
        upsert_count = 0
        with pg_hook.get_conn() as conn:
            with conn.cursor() as cur:
                for coin in coins:
                    is_active = coin.get('is_active')
                    rank = coin.get('rank')
                    symbol = coin.get('symbol')
                    cmc_id = coin.get('id')
                    name = coin.get('name')
                    slug = coin.get('slug')
                    first_history = coin.get('first_historical_data')
                    platform = coin.get('platform')
                    
                    # Filtering Logic
                    if not is_active:
                        continue
                    if rank is None or rank > 1000:
                        continue
                    
                    eth_address = None
                    if platform:
                        # Only include if platform is Ethereum
                        if platform.get('slug') != 'ethereum':
                            continue
                        eth_address = platform.get('token_address')
                    elif symbol not in ['BTC', 'LTC', 'BCH', 'XRP', 'DOGE', 'ETH']:
                        # If no platform, it's typically a native coin. 
                        # We only want ETH and peers, or ETH tokens.
                        # Since user specifically asked for "platform is ethereum", 
                        # tokens are the primary target, but obviously we want to keep/allow ETH too.
                        if symbol != 'ETH':
                            continue

                    if not symbol:
                        continue
                        
                    symbol = symbol.upper()
                    
                    # Upsert: Insert or Update everything
                    # We use symbol as PK. 
                    cur.execute("""
                        INSERT INTO coin (
                            symbol, name, slug, cmc_id, cmc_rank, 
                            ethereum_address, first_historical_data
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol) DO UPDATE SET
                            name = EXCLUDED.name,
                            slug = EXCLUDED.slug,
                            cmc_id = EXCLUDED.cmc_id,
                            cmc_rank = EXCLUDED.cmc_rank,
                            ethereum_address = EXCLUDED.ethereum_address,
                            first_historical_data = EXCLUDED.first_historical_data;
                    """, (
                        symbol[:8], name, slug, cmc_id, rank, 
                        eth_address, first_history
                    ))
                    upsert_count += 1
                    
            conn.commit()
            
        logging.info(f"Upserted/Updated {upsert_count} coins matching criteria into coin table")

    sync_cmc_map()
