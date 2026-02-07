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
        all_coins = data.get('data', [])
        logging.info(f"Received {len(all_coins)} coins from CMC")

        # 1. Detailed Filtering and ID selection
        target_ids = []
        coin_meta_map = {} # id -> partial meta
        
        for coin in all_coins:
            is_active = coin.get('is_active')
            rank = coin.get('rank')
            symbol = coin.get('symbol')
            cmc_id = coin.get('id')
            platform = coin.get('platform')
            
            if not is_active:
                continue
                
            is_target = False
            if platform and platform.get('slug') == 'ethereum':
                is_target = True
            elif symbol in ['ETH', 'BTC', 'WBTC']: # Standard large caps
                is_target = True
                
            if is_target:
                target_ids.append(str(cmc_id))
                coin_meta_map[cmc_id] = coin

        logging.info(f"Identified {len(target_ids)} active Ethereum-related coins for detailed metadata fetch")

        # 2. Batch fetch detailed info (Logo, Decimals)
        # CMC allows batching up to ~100 IDs
        INFO_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"
        detailed_info = {}
        
        for i in range(0, len(target_ids), 100):
            batch = target_ids[i:i+100]
            logging.info(f"Fetching metadata for batch {i//100 + 1}...")
            res = requests.get(INFO_URL, headers=headers, params={'id': ','.join(batch)})
            if res.status_code == 200:
                batch_data = res.json().get('data', {})
                detailed_info.update(batch_data)
            else:
                logging.error(f"Failed to fetch metadata batch: {res.text}")

        # 3. Upsert to Postgres
        pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        upsert_count = 0
        
        with pg_hook.get_conn() as conn:
            with conn.cursor() as cur:
                for cmc_id_str, info in detailed_info.items():
                    cmc_id = int(cmc_id_str)
                    symbol = info.get('symbol', '').upper()
                    name = info.get('name')
                    slug = info.get('slug')
                    logo = info.get('logo') # Image URL
                    rank = info.get('cmc_rank')
                    
                    # Extract Ethereum details from contract_address list
                    eth_address = None
                    # We keep existing decimals if available in DB by NOT overwriting with 18 
                    # unless we are sure. But since this is a new insert, we need a default.
                    decimals = 18 
                    
                    contracts = info.get('contract_address', [])
                    if isinstance(contracts, list):
                        for c in contracts:
                            plt = c.get('platform', {})
                            if plt.get('name') == 'Ethereum' or plt.get('coin', {}).get('slug') == 'ethereum':
                                eth_address = c.get('contract_address')
                                break
                    
                    # Manual Override for known major natives/tokens
                    if symbol == 'ETH': decimals = 18
                    elif symbol in ['BTC', 'WBTC']: decimals = 8
                    elif symbol in ['USDC', 'USDT', 'EURC']: decimals = 6
                    
                    if not symbol: continue

                    cur.execute("""
                        INSERT INTO coin (
                            symbol, name, slug, cmc_id, cmc_rank, 
                            ethereum_address, image_url, decimals
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol) DO UPDATE SET
                            name = EXCLUDED.name,
                            slug = EXCLUDED.slug,
                            cmc_id = EXCLUDED.cmc_id,
                            cmc_rank = EXCLUDED.cmc_rank,
                            ethereum_address = EXCLUDED.ethereum_address,
                            image_url = EXCLUDED.image_url,
                            decimals = CASE 
                                WHEN EXCLUDED.decimals != 18 THEN EXCLUDED.decimals 
                                ELSE coin.decimals 
                            END;
                    """, (
                        symbol[:8], name, slug, cmc_id, rank, 
                        eth_address, logo, decimals
                    ))
                    upsert_count += 1
                    
            conn.commit()
            
        logging.info(f"Successfully synced detailed metadata for {upsert_count} coins")

    sync_cmc_map()
