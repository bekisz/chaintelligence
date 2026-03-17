from airflow import DAG
from airflow.sdk import task, Param, Asset
from airflow.models import Variable
from airflow.providers.postgres.hooks.postgres import PostgresHook
from datetime import timedelta
import logging
import pendulum
import os
import requests
import time

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=3),
}

# Asset for data-aware scheduling
coin_table_asset = Asset("postgres://postgres:5432/chaintelligence/public/coin")

@task.branch
def check_mapping_freshness(force_update: bool = False):
    """
    Check if CMC coin mapping needs refresh.
    Checks Airflow Variable 'CMC_LAST_MAPPING_SYNC', fallbacks to DB 'cmc_last_updated'.
    """
    # Handle parameter string -> bool (if triggered from UI/CLI with string)
    if isinstance(force_update, str):
        force_update = force_update.lower() in ('true', '1', 'yes')
    
    if force_update:
        logging.info("🔄 Force update flag is set - proceeding to sync")
        return 'cmc_map_fetch'

    # 1. Check Airflow Variable
    last_sync_str = Variable.get("CMC_LAST_MAPPING_SYNC", default_var=None)
    last_sync = None
    
    if last_sync_str:
        try:
            last_sync = pendulum.parse(last_sync_str)
            logging.info(f"📍 Found last sync timestamp in Airflow Variables: {last_sync}")
        except Exception as e:
            logging.warning(f"⚠️ Failed to parse CMC_LAST_MAPPING_SYNC variable '{last_sync_str}': {e}")

    # 2. Fallback to DB
    if not last_sync:
        pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        last_sync_query = "SELECT MAX(cmc_last_updated) FROM coin WHERE cmc_last_updated IS NOT NULL"
        try:
            last_sync_result = pg_hook.get_first(last_sync_query)
            last_sync = last_sync_result[0] if last_sync_result and last_sync_result[0] else None
            
            if last_sync:
                if isinstance(last_sync, str):
                    last_sync = pendulum.parse(last_sync)
                elif not hasattr(last_sync, 'diff'):
                    last_sync = pendulum.instance(last_sync)
                logging.info(f"📍 Found last sync timestamp in Database: {last_sync}")
        except Exception as e:
            logging.warning(f"⚠️ Failed to fetch last sync from DB: {e}")

    if last_sync:
        age_days = pendulum.now('UTC').diff(last_sync).in_days()
        logging.info(f"📅 Last mapping sync was {age_days} days ago")
        if age_days <= 7:
            logging.info("✅ Mapping is fresh (<= 7 days). Skipping sync.")
            return 'skip_sync'
    else:
        logging.info("⚠️ No previous mapping sync found.")

    logging.info("🚀 Proceeding to CMC mapping sync.")
    return 'cmc_map_fetch'

@task
def cmc_map_fetch(max_rank: int = 200):
    """
    Fetch Basic CMC Map and identify target coins.
    """
    # Handle parameter string -> int (if triggered from UI/CLI with string)
    if isinstance(max_rank, str):
        try:
            max_rank = int(max_rank)
        except ValueError:
            max_rank = 200

    logging.info(f"🔄 Fetching CMC basic map (Max Rank: {max_rank})...")
    
    CMC_API_KEY = os.getenv('CMC_API_KEY')
    CMC_MAP_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
    
    if not CMC_API_KEY:
        raise ValueError("CMC_API_KEY environment variable is not set")

    headers = {'X-CMC_PRO_API_KEY': CMC_API_KEY, 'Accept': 'application/json'}
    
    res = requests.get(CMC_MAP_URL, headers=headers, timeout=30)
    res.raise_for_status()
    all_coins = res.json().get('data', [])
    
    target_ids = []
    rank_map = {}
    
    for coin in all_coins:
        if not coin.get('is_active'): continue
        
        should_add = False
        slug = (coin.get('platform') or {}).get('slug')
        sym = coin.get('symbol')
        rank = coin.get('rank')
        cid = coin.get('id')
        
        # Inclusion rules
        if slug == 'ethereum': should_add = True
        elif sym in ['ETH', 'BTC', 'WBTC', 'SOL', 'BNB', 'AVAX', 'MATIC', 'ADA', 'DOT']: should_add = True
        elif rank and rank <= max_rank: should_add = True
        
        if should_add:
            target_ids.append(str(cid))
            if rank: rank_map[str(cid)] = rank
            
    # Sort target_ids by rank (if present) just in case, though they are usually sorted from CMC
    target_ids = sorted(target_ids, key=lambda x: rank_map.get(x, 999999))[:20]
    rank_map = {cid: rank_map[cid] for cid in target_ids if cid in rank_map}
    
    logging.info(f"Identified {len(target_ids)} coins for metadata sync (truncated to 20).")
    return {"target_ids": target_ids, "rank_map": rank_map}

@task
def cmc_info_fetch(mapping_data: dict):
    """
    Fetch detailed CMC metadata for target coins.
    """
    target_ids = mapping_data.get('target_ids', [])
    rank_map = mapping_data.get('rank_map', {})
    
    if not target_ids:
        logging.info("No target IDs to fetch info for.")
        return {"detailed_info": {}, "rank_map": rank_map}

    CMC_API_KEY = os.getenv('CMC_API_KEY')
    CMC_INFO_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"
    
    if not CMC_API_KEY:
        raise ValueError("CMC_API_KEY environment variable is not set")

    headers = {'X-CMC_PRO_API_KEY': CMC_API_KEY, 'Accept': 'application/json'}
    
    detailed_info = {}
    # Fetch in batches of 50
    for i in range(0, len(target_ids), 50):
        batch = target_ids[i:i+50]
        logging.info(f"Fetching metadata for batch {i} to {i+len(batch)} of {len(target_ids)}...")
        r = requests.get(CMC_INFO_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
        if r.status_code == 200:
            batch_data = r.json().get('data', {})
            for cid, info in batch_data.items():
                # Extract only specific fields to avoid XCom serialization issues with huge integers (e.g. supply)
                detailed_info[cid] = {
                    'symbol': info.get('symbol'),
                    'name': info.get('name'),
                    'slug': info.get('slug'),
                    'logo': info.get('logo'),
                    'contract_address': info.get('contract_address')
                }
        elif r.status_code == 429:
             logging.warning("Rate limit hit, waiting 60s...")
             time.sleep(60)
             r = requests.get(CMC_INFO_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
             if r.status_code == 200:
                batch_data = r.json().get('data', {})
                for cid, info in batch_data.items():
                    detailed_info[cid] = {
                        'symbol': info.get('symbol'),
                        'name': info.get('name'),
                        'slug': info.get('slug'),
                        'logo': info.get('logo'),
                        'contract_address': info.get('contract_address')
                    }
        
        time.sleep(0.5) # gentle rate limit
    
    logging.info(f"Fetched detailed info for {len(detailed_info)} coins.")
    return {"detailed_info": detailed_info, "rank_map": rank_map}

@task(outlets=[coin_table_asset])
def cmc_upsert_to_db(fetch_result: dict):
    """
    Upsert detailed CMC info to the database.
    """
    detailed_info = fetch_result.get('detailed_info', {})
    rank_map = fetch_result.get('rank_map', {})
    
    if not detailed_info:
        logging.info("No detailed info to upsert.")
        return 0

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    upsert_count = 0
    now = pendulum.now('UTC')
    
    # Sort items by rank (higher rank / lower number first)
    sorted_items = sorted(
        detailed_info.items(), 
        key=lambda x: rank_map.get(str(x[0]), 999999)
    )
    
    # Upsert to DB
    with pg_hook.get_conn() as conn:
        with conn.cursor() as cur:
             for cmc_id_str, info in sorted_items:
                cmc_id = int(cmc_id_str)
                symbol = info.get('symbol', '').upper()
                name = info.get('name')
                slug = info.get('slug')
                logo = info.get('logo')
                rank = rank_map.get(cmc_id_str)
                
                eth_address = None
                decimals = 18
                contracts = info.get('contract_address', [])
                if isinstance(contracts, list):
                    for c in contracts:
                        if c.get('platform', {}).get('name') == 'Ethereum':
                            eth_address = c.get('contract_address')
                            break
                
                if symbol == 'ETH': 
                    eth_address = '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
                    decimals = 18
                elif symbol in ['BTC', 'WBTC']: decimals = 8
                elif symbol in ['USDC', 'USDT', 'EURC']: decimals = 6
                
                if not symbol or not eth_address: 
                    continue
                
                cur.execute("""
                    INSERT INTO coin (
                        symbol, name, slug, cmc_id, cmc_rank, 
                        ethereum_address, image_url, decimals, cmc_last_updated
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name = EXCLUDED.name, slug = EXCLUDED.slug, cmc_id = EXCLUDED.cmc_id,
                        cmc_rank = EXCLUDED.cmc_rank, ethereum_address = COALESCE(coin.ethereum_address, EXCLUDED.ethereum_address),
                        image_url = EXCLUDED.image_url, cmc_last_updated = EXCLUDED.cmc_last_updated,
                        decimals = CASE WHEN EXCLUDED.decimals != 18 THEN EXCLUDED.decimals ELSE coin.decimals END
                """, (symbol[:8], name, slug, cmc_id, rank, eth_address, logo, decimals, now))
                upsert_count += 1
        conn.commit()

    Variable.set("CMC_LAST_MAPPING_SYNC", now.to_iso8601_string())
    logging.info(f"✅ CMC mapping sync complete. Upserted {upsert_count} coins.")
    return upsert_count

@task(outlets=[coin_table_asset])
def skip_sync():
    logging.info("⏭️ Mapping is already fresh. Skipping.")

with DAG(
    dag_id='coin_ingestion',
    default_args=default_args,
    description='Sync CoinMarketCap coin-to-address mapping',
    schedule='@weekly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['metadata', 'coinmarketcap'],
    params={
        'force_update': Param(False, description='Force mapping refresh even if fresh'),
        'max_rank': Param(200, description='Maximum CMC rank to fetch metadata for'),
    },
) as dag:
    
    check = check_mapping_freshness(force_update="{{ params.force_update }}")
    map_fetch = cmc_map_fetch(max_rank="{{ params.max_rank }}")
    info_fetch = cmc_info_fetch(map_fetch)
    upsert = cmc_upsert_to_db(info_fetch)
    skip = skip_sync()
    
    check >> [map_fetch, skip]
