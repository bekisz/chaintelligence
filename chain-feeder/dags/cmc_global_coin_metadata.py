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
    if isinstance(force_update, str):
        force_update = force_update.lower() in ('true', '1', 'yes')
    
    if force_update:
        logging.info("🔄 Force update flag is set - proceeding to sync")
        return 'cmc_map_fetch'

    last_sync_str = Variable.get("CMC_LAST_MAPPING_SYNC", default_var=None)
    last_sync = None
    
    if last_sync_str:
        try:
            last_sync = pendulum.parse(last_sync_str)
            logging.info(f"📍 Found last sync timestamp in Airflow Variables: {last_sync}")
        except Exception as e:
            logging.warning(f"⚠️ Failed to parse CMC_LAST_MAPPING_SYNC variable '{last_sync_str}': {e}")

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
def cmc_map_fetch(max_rank: int = 10000, max_coins: int = 15000):
    """
    Fetch Basic CMC Map and identify all target coins.
    """
    if isinstance(max_rank, str):
        try:
            max_rank = int(max_rank)
        except ValueError:
            max_rank = 10000

    if isinstance(max_coins, str):
        try:
            max_coins = int(max_coins)
        except ValueError:
            max_coins = 15000

    logging.info(f"🔄 Fetching full CMC basic map (Max Rank: {max_rank})...")
    
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
        if not coin.get('is_active'):
            continue
        
        cid = str(coin.get('id'))
        rank = coin.get('rank')
        target_ids.append(cid)
        if rank:
            rank_map[cid] = rank

    if max_coins and max_coins > 0 and len(target_ids) > max_coins:
        target_ids = target_ids[:max_coins]

    logging.info(f"Identified {len(target_ids)} coins for full metadata sync.")
    return {"target_ids": target_ids, "rank_map": rank_map}

def load_chains_config():
    paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'chains.yaml')),
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'chains.yaml')),
        '/opt/airflow/config/chains.yaml'
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r') as f:
                import yaml
                return yaml.safe_load(f)
    raise FileNotFoundError("Could not locate chains.yaml configuration file")

def load_manual_contracts_config():
    paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'manual-contracts.yaml')),
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'manual-contracts.yaml')),
        '/opt/airflow/config/manual-contracts.yaml'
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r') as f:
                import yaml
                return yaml.safe_load(f)
    return {'contracts': []}

@task(outlets=[coin_table_asset])
def cmc_fetch_and_upsert_to_db(mapping_data: dict) -> int:
    """
    Fetch detailed CMC metadata & quotes for target coins in batch streams and upsert directly into Postgres.
    Eliminates XCom payload size issues and local/shared disk dependencies for distributed workers.
    """
    target_ids = mapping_data.get('target_ids', [])
    rank_map = mapping_data.get('rank_map', {})

    if not target_ids:
        logging.info("No target IDs to fetch info for.")
        return 0

    CMC_API_KEY = os.getenv('CMC_API_KEY')
    CMC_INFO_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"
    CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"

    if not CMC_API_KEY:
        raise ValueError("CMC_API_KEY environment variable is not set")

    headers = {'X-CMC_PRO_API_KEY': CMC_API_KEY, 'Accept': 'application/json'}

    chains_config = load_chains_config()
    platform_to_chain = {
        c['cmc_platform_name']: c['name'] 
        for c in chains_config['chains']
    }
    
    family_coins = set()
    try:
        import yaml
        candidate_paths = [
            os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'config', 'coin-families.yml'),
            os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config', 'coin-families.yml')),
            os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'coin-families.yml')),
        ]
        families_file = next((p for p in candidate_paths if os.path.exists(p)), candidate_paths[0])
        with open(families_file, 'r') as f:
            config = yaml.safe_load(f)
            families = config.get('coin-families', [])
            for family in families:
                coins = family.get('coin-list', []) or []
                for coin in coins:
                    family_coins.add(coin.upper())
                inc_coins = family.get('include-coin', []) or []
                for coin in inc_coins:
                    family_coins.add(coin.upper())
    except Exception as e:
        logging.warning(f"Could not load coin-families.yml in upsert: {e}")

    FALLBACK_METADATA = {
        'ETH': {'name': 'Ethereum', 'slug': 'ethereum', 'ethereum_address': '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 'decimals': 18},
        'WETH': {'name': 'Wrapped Ether', 'slug': 'wrapped-ether', 'ethereum_address': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2', 'decimals': 18},
        'USDC': {'name': 'USD Coin', 'slug': 'usd-coin', 'ethereum_address': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', 'decimals': 6},
        'USDT': {'name': 'Tether', 'slug': 'tether', 'ethereum_address': '0xdac17f958d2ee523a2206206994597c13d831ec7', 'decimals': 6},
        'DAI': {'name': 'Dai', 'slug': 'dai', 'ethereum_address': '0x6b175474e89094c44da98b954eedeac495271d0f', 'decimals': 18},
        'WBTC': {'name': 'Wrapped Bitcoin', 'slug': 'wrapped-bitcoin', 'ethereum_address': '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599', 'decimals': 8},
        'STETH': {'name': 'Liquid Staked Ether', 'slug': 'staked-ether', 'ethereum_address': '0xae7ab96520de3a18e5e111b5eaab095312d7fe84', 'decimals': 18},
        'WSTETH': {'name': 'Wrapped Lido Staked Ether', 'slug': 'wrapped-steth', 'ethereum_address': '0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0', 'decimals': 18},
        'RETH': {'name': 'Rocket Pool ETH', 'slug': 'rocket-pool-eth', 'ethereum_address': '0xae78736cd615f374d3085123a210448e74fc6393', 'decimals': 18},
        'PENDLE': {'name': 'Pendle', 'slug': 'pendle', 'ethereum_address': '0x808507121b80c0546a1d48931130635e169fa121', 'decimals': 18},
        'ENA': {'name': 'Ethena', 'slug': 'ethena', 'ethereum_address': '0x57e114b691db790c35207b2e685d4a43181e6061', 'decimals': 18},
        'USDE': {'name': 'Ethena USDe', 'slug': 'ethena-usde', 'ethereum_address': '0x4c9edd5852cd14fe7183fdb42c274d2808b04a55', 'decimals': 18},
        'SUSDS': {'name': 'Savings USDS', 'slug': 'savings-usds', 'ethereum_address': '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd', 'decimals': 18}
    }

    DECIMALS_MAP = {
        'ETH': 18, 'WETH': 18, 'USDC': 6, 'USDT': 6, 'DAI': 18, 'WBTC': 8, 'EURC': 6,
        'BTC': 8, 'CBBTC': 8, 'TBTC': 18, 'FBTC': 8, 'LBTC': 8, 'KBTC': 8, 'USDE': 18,
        'SUSDE': 18
    }

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    now = pendulum.now('UTC')
    upsert_count = 0
    fetched_symbols = set()

    with pg_hook.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM chain")
            chain_map = {row[1].lower(): row[0] for row in cur.fetchall()}
            chain_map['bsc'] = chain_map.get('bnb')

            # 1. Ingest manual overrides from config file
            manual_config = load_manual_contracts_config()
            manual_contracts = manual_config.get('contracts', [])
            for mc in manual_contracts:
                symbol = mc.get('symbol', '').upper()
                name = mc.get('name')
                slug = mc.get('slug')
                chain_name = mc.get('chain', '').lower()
                contract_addr = mc.get('address')
                mc_decimals = mc.get('decimals', 18)
                is_native = mc.get('is_native', False)
                
                if not symbol or not chain_name or not contract_addr:
                    continue
                    
                chain_id = chain_map.get(chain_name)
                if not chain_id:
                    logging.warning(f"Unknown chain '{chain_name}' in manual overrides, skipping.")
                    continue
                
                # Insert/update coin record
                cur.execute("""
                    INSERT INTO coin (symbol, name, slug, decimals, cmc_last_updated)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name = COALESCE(EXCLUDED.name, coin.name),
                        slug = COALESCE(EXCLUDED.slug, coin.slug)
                    RETURNING coin_id
                """, (symbol[:10], name, slug, mc_decimals, now))
                coin_id = cur.fetchone()[0]
                
                # Insert/update contract address with manual priority (score 100)
                cur.execute("""
                    INSERT INTO coin_contract (
                        coin_id, chain_id, contract_address, decimals,
                        is_native, source, confidence_score, verified_at, tracked
                    )
                    VALUES (%s, %s, %s, %s, %s, 'manual', 100, NOW(), true)
                    ON CONFLICT (coin_id, chain_id) DO UPDATE SET
                        contract_address = EXCLUDED.contract_address,
                        decimals = COALESCE(EXCLUDED.decimals, coin_contract.decimals),
                        is_native = EXCLUDED.is_native,
                        source = EXCLUDED.source,
                        confidence_score = EXCLUDED.confidence_score,
                        verified_at = EXCLUDED.verified_at
                    WHERE EXCLUDED.confidence_score >= coin_contract.confidence_score
                """, (coin_id, chain_id, contract_addr.lower(), mc_decimals, is_native))
                upsert_count += 1
            conn.commit()

            # 2. Ingest batches from CMC
            for i in range(0, len(target_ids), 50):
                batch = target_ids[i:i+50]
                logging.info(f"Fetching & upserting batch {i}-{i+len(batch)} of {len(target_ids)}...")
                
                # Fetch info batch
                r_info = requests.get(CMC_INFO_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
                if r_info.status_code == 429:
                    logging.warning("Rate limit hit on info, waiting 60s...")
                    time.sleep(60)
                    r_info = requests.get(CMC_INFO_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
                
                batch_info = {}
                if r_info.status_code == 200:
                    batch_info = r_info.json().get('data', {})

                time.sleep(0.3)

                # Fetch quotes batch
                r_quotes = requests.get(CMC_QUOTES_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
                if r_quotes.status_code == 429:
                    logging.warning("Rate limit hit on quotes, waiting 60s...")
                    time.sleep(60)
                    r_quotes = requests.get(CMC_QUOTES_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
                
                batch_quotes = {}
                if r_quotes.status_code == 200:
                    batch_quotes = r_quotes.json().get('data', {})

                # Upsert batch directly to DB
                for cid_str in batch:
                    info = batch_info.get(cid_str, {})
                    quote_info = batch_quotes.get(cid_str, {})
                    quote = quote_info.get('quote', {}).get('USD', {})

                    symbol = info.get('symbol', '').upper()
                    if not symbol:
                        continue

                    fetched_symbols.add(symbol)
                    name = info.get('name')
                    slug = info.get('slug')
                    logo = info.get('logo')
                    rank = rank_map.get(cid_str)
                    
                    try:
                        cmc_id = int(cid_str)
                    except ValueError:
                        cmc_id = None

                    decimals = info.get('decimals', 18)
                    contracts = info.get('contract_address', [])
                    chain_addresses = {}
                    if isinstance(contracts, list):
                        for c in contracts:
                            platform_name = c.get('platform', {}).get('name', '')
                            chain = platform_to_chain.get(platform_name)
                            if chain:
                                chain_addresses[chain] = c.get('contract_address')

                    if symbol in DECIMALS_MAP:
                        decimals = DECIMALS_MAP[symbol]

                    for c_conf in chains_config['chains']:
                        ch_name = c_conf['name']
                        native_sym = c_conf['native_coin_symbol']
                        if ch_name not in chain_addresses and symbol == native_sym:
                            if ch_name == 'ethereum':
                                chain_addresses[ch_name] = '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
                            else:
                                chain_addresses[ch_name] = '0x0000000000000000000000000000000000000000'

                    if not chain_addresses and symbol in family_coins:
                        fallback = FALLBACK_METADATA.get(symbol)
                        if fallback:
                            chain_addresses['ethereum'] = fallback.get('ethereum_address')
                            decimals = fallback.get('decimals', decimals)

                    cur.execute("""
                        INSERT INTO coin (
                            symbol, name, slug, cmc_id, cmc_rank,
                            image_url, decimals, cmc_last_updated,
                            price, price_timestamp, market_cap,
                            percent_change_1h, percent_change_24h, percent_change_7d,
                            percent_change_30d, percent_change_60d, percent_change_90d,
                            fully_diluted_market_cap, market_cap_dominance, tvl,
                            total_supply, circulating_supply, max_supply
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol) DO UPDATE SET
                            name = EXCLUDED.name,
                            slug = EXCLUDED.slug,
                            cmc_id = COALESCE(EXCLUDED.cmc_id, coin.cmc_id),
                            cmc_rank = COALESCE(EXCLUDED.cmc_rank, coin.cmc_rank),
                            image_url = EXCLUDED.image_url,
                            cmc_last_updated = EXCLUDED.cmc_last_updated,
                            price = EXCLUDED.price,
                            price_timestamp = EXCLUDED.price_timestamp,
                            market_cap = EXCLUDED.market_cap,
                            percent_change_1h = EXCLUDED.percent_change_1h,
                            percent_change_24h = EXCLUDED.percent_change_24h,
                            percent_change_7d = EXCLUDED.percent_change_7d,
                            percent_change_30d = EXCLUDED.percent_change_30d,
                            percent_change_60d = EXCLUDED.percent_change_60d,
                            percent_change_90d = EXCLUDED.percent_change_90d,
                            fully_diluted_market_cap = EXCLUDED.fully_diluted_market_cap,
                            market_cap_dominance = EXCLUDED.market_cap_dominance,
                            tvl = EXCLUDED.tvl,
                            total_supply = EXCLUDED.total_supply,
                            circulating_supply = EXCLUDED.circulating_supply,
                            max_supply = EXCLUDED.max_supply,
                            decimals = CASE WHEN EXCLUDED.decimals != 18 THEN EXCLUDED.decimals ELSE coin.decimals END
                        RETURNING coin_id
                    """,
                    (
                        symbol[:10], name, slug, cmc_id, rank, logo, decimals, now,
                        quote.get('price'), quote.get('last_updated'), quote.get('market_cap'),
                        quote.get('percent_change_1h'), quote.get('percent_change_24h'), quote.get('percent_change_7d'),
                        quote.get('percent_change_30d'), quote.get('percent_change_60d'), quote.get('percent_change_90d'),
                        quote.get('fully_diluted_market_cap'), quote.get('market_cap_dominance'), quote.get('tvl'),
                        quote.get('total_supply'), quote.get('circulating_supply'), quote.get('max_supply')
                    ))
                    coin_id = cur.fetchone()[0]

                    for chain_name, contract_addr in chain_addresses.items():
                        if not contract_addr or not isinstance(contract_addr, str):
                            continue
                        ch_decimals = decimals
                        if symbol in DECIMALS_MAP:
                            ch_decimals = DECIMALS_MAP[symbol]
                        
                        is_native = False
                        for c_conf in chains_config['chains']:
                            if c_conf['name'] == chain_name and symbol == c_conf['native_coin_symbol']:
                                is_native = True
                                break
                        
                        chain_id = chain_map.get(chain_name.lower())
                        if chain_id is not None:
                            cur.execute("""
                                INSERT INTO coin_contract (
                                    coin_id, chain_id, contract_address, decimals,
                                    is_native, source, confidence_score, verified_at, tracked
                                )
                                VALUES (%s, %s, %s, %s, %s, 'cmc', 90, NOW(), true)
                                ON CONFLICT (coin_id, chain_id) DO UPDATE SET
                                    contract_address = EXCLUDED.contract_address,
                                    decimals = COALESCE(EXCLUDED.decimals, coin_contract.decimals),
                                    source = EXCLUDED.source,
                                    confidence_score = EXCLUDED.confidence_score,
                                    verified_at = EXCLUDED.verified_at
                                WHERE EXCLUDED.confidence_score >= coin_contract.confidence_score
                            """, (coin_id, chain_id, contract_addr.lower(), ch_decimals, is_native))

                    upsert_count += 1
                
                conn.commit()
                time.sleep(0.3)

            # Handle missing family coins fallback
            missing_family_symbols = family_coins - fetched_symbols
            for sym in missing_family_symbols:
                fallback = FALLBACK_METADATA.get(sym)
                if fallback:
                    cur.execute("""
                        INSERT INTO coin (symbol, name, slug, decimals, cmc_last_updated)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (symbol) DO UPDATE SET name = EXCLUDED.name
                        RETURNING coin_id
                    """, (sym[:10], fallback['name'], fallback['slug'], fallback.get('decimals', 18), now))
                    coin_id = cur.fetchone()[0]
                    eth_chain_id = chain_map.get('ethereum')
                    if eth_chain_id:
                        cur.execute("""
                            INSERT INTO coin_contract (coin_id, chain_id, contract_address, decimals, is_native, source, confidence_score, verified_at, tracked)
                            VALUES (%s, %s, %s, %s, %s, 'fallback', 90, NOW(), true)
                            ON CONFLICT (coin_id, chain_id) DO UPDATE SET contract_address = EXCLUDED.contract_address
                        """, (coin_id, eth_chain_id, fallback['ethereum_address'].lower(), fallback.get('decimals', 18), (sym == 'ETH')))
                    upsert_count += 1
            conn.commit()

    Variable.set("CMC_LAST_MAPPING_SYNC", now.to_iso8601_string())
    logging.info(f"✅ CMC mapping batch fetch & sync complete. Upserted {upsert_count} coins.")
    return upsert_count

@task(outlets=[coin_table_asset])
def fallback_contract_ingestion(upsert_count: int):
    """
    Fallback contract resolution task:
    Queries DexScreener & CoinGecko APIs to resolve contract addresses for coins missing from CMC.
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    with pg_hook.get_conn() as conn:
        import sys
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'include')))
        from contract_ingestion import MultiSourceContractEngine
        engine = MultiSourceContractEngine()
        resolved = engine.resolve_missing_contracts(conn)
        logging.info(f"✅ Fallback multi-source contract resolution complete. Resolved {resolved} missing coins.")
    return resolved

@task(outlets=[coin_table_asset])
def skip_sync():
    logging.info("⏭️ Mapping is already fresh. Skipping.")

with DAG(
    max_active_runs=1,
    dag_id='cmc_global_coin_metadata',
    default_args=default_args,
    description='Sync CoinMarketCap coin-to-address mapping and run multi-source fallback resolution',
    schedule='@weekly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['metadata', 'coinmarketcap'],
    params={
        'force_update': Param(False, description='Force mapping refresh even if fresh'),
        'max_rank': Param(10000, description='Maximum CMC rank to fetch metadata for'),
        'max_coins': Param(15000, description='Maximum number of coins to sync'),
    },
) as dag:
    
    check = check_mapping_freshness(force_update="{{ params.force_update }}")
    map_fetch = cmc_map_fetch(max_rank="{{ params.max_rank }}", max_coins="{{ params.max_coins }}")
    sync = cmc_fetch_and_upsert_to_db(map_fetch)
    fallback = fallback_contract_ingestion(sync)
    skip = skip_sync()
    
    check >> [map_fetch, skip]
    map_fetch >> sync >> fallback
