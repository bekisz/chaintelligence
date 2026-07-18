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
def cmc_map_fetch(max_rank: int = 200, max_coins: int = 500):
    """
    Fetch Basic CMC Map and identify target coins.
    """
    # Handle parameter string -> int (if triggered from UI/CLI with string)
    if isinstance(max_rank, str):
        try:
            max_rank = int(max_rank)
        except ValueError:
            max_rank = 200

    # Handle max_coins parameter string
    if isinstance(max_coins, str):
        try:
            max_coins = int(max_coins)
        except ValueError:
            max_coins = 500

    logging.info(f"🔄 Fetching CMC basic map (Max Rank: {max_rank})...")
    
    # Load all coins configured in coin-families.yml to ensure they are always fetched
    family_coins = set()
    try:
        import yaml
        ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        families_file = os.path.join(ROOT_DIR, 'include', 'config', 'coin-families.yml')
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
        logging.info(f"Loaded {len(family_coins)} coins from coin-families config: {family_coins}")
    except Exception as e:
        logging.warning(f"Could not load coin-families.yml config: {e}")

    CMC_API_KEY = os.getenv('CMC_API_KEY')
    CMC_MAP_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
    
    if not CMC_API_KEY:
        raise ValueError("CMC_API_KEY environment variable is not set")

    headers = {'X-CMC_PRO_API_KEY': CMC_API_KEY, 'Accept': 'application/json'}
    
    res = requests.get(CMC_MAP_URL, headers=headers, timeout=30)
    res.raise_for_status()
    all_coins = res.json().get('data', [])
    
    family_target_ids = []
    other_target_ids = []
    rank_map = {}
    found_family_symbols = set()
    
    for coin in all_coins:
        if not coin.get('is_active'): continue
        
        slug = (coin.get('platform') or {}).get('slug')
        sym = coin.get('symbol', '').upper()
        rank = coin.get('rank')
        cid = coin.get('id')
        
        if sym in family_coins:
            family_target_ids.append(str(cid))
            found_family_symbols.add(sym)
            if rank: rank_map[str(cid)] = rank
        else:
            should_add = False
            if slug == 'ethereum': should_add = True
            elif sym in ['ETH', 'BTC', 'WBTC', 'SOL', 'BNB', 'AVAX', 'MATIC', 'ADA', 'DOT']: should_add = True
            elif rank and rank <= max_rank: should_add = True
            
            if should_add:
                other_target_ids.append(str(cid))
                if rank: rank_map[str(cid)] = rank
                
    missing_from_map = family_coins - found_family_symbols
    if missing_from_map:
        logging.info(f"Family coins completely missing from CMC map: {missing_from_map}")

    # Sort other target IDs by rank
    other_target_ids = sorted(other_target_ids, key=lambda x: rank_map.get(x, 999999))
    
    # Pad the remaining limit with other target IDs
    limit_other = max(0, max_coins - len(family_target_ids))
    target_ids = family_target_ids + other_target_ids[:limit_other]
    rank_map = {cid: rank_map[cid] for cid in target_ids if cid in rank_map}
    
    logging.info(f"Identified {len(target_ids)} coins for metadata sync (family: {len(family_target_ids)}, other: {len(target_ids) - len(family_target_ids)}, limited to {max_coins}).")
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
    CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"

    if not CMC_API_KEY:
        raise ValueError("CMC_API_KEY environment variable is not set")

    headers = {'X-CMC_PRO_API_KEY': CMC_API_KEY, 'Accept': 'application/json'}

    detailed_info = {}
    # First fetch info (symbol, name, slug, logo, contract_address)
    for i in range(0, len(target_ids), 50):
        batch = target_ids[i:i+50]
        logging.info(f"Fetching metadata for batch {i}-{i+len(batch)} of {len(target_ids)}...")
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
        time.sleep(0.5)

    # Second fetch price/quote data for same IDs
    for i in range(0, len(target_ids), 50):
        batch = target_ids[i:i+50]
        logging.info(f"Fetching price data for batch {i}-{i+len(batch)} of {len(target_ids)}...")
        r = requests.get(CMC_QUOTES_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
        if r.status_code == 200:
            batch_data = r.json().get('data', {})
            for cid, quote_info in batch_data.items():
                quote = quote_info.get('quote', {}).get('USD', {})
                if cid in detailed_info:
                    detailed_info[cid].update({
                        'price': quote.get('price'),
                        'price_timestamp': quote.get('last_updated'),
                        'market_cap': quote.get('market_cap'),
                        'fully_diluted_market_cap': quote.get('fully_diluted_market_cap'),
                        'market_cap_dominance': quote.get('market_cap_dominance'),
                        'tvl': quote.get('tvl'),
                        'total_supply': quote.get('total_supply'),
                        'circulating_supply': quote.get('circulating_supply'),
                        'max_supply': quote.get('max_supply'),
                        'percent_change_1h': quote.get('percent_change_1h'),
                        'percent_change_24h': quote.get('percent_change_24h'),
                        'percent_change_7d': quote.get('percent_change_7d'),
                        'percent_change_30d': quote.get('percent_change_30d'),
                        'percent_change_60d': quote.get('percent_change_60d'),
                        'percent_change_90d': quote.get('percent_change_90d')
                    })
        elif r.status_code == 429:
            logging.warning("Rate limit hit on quotes, waiting 60s...")
            time.sleep(60)
            r = requests.get(CMC_QUOTES_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
            if r.status_code == 200:
                batch_data = r.json().get('data', {})
                for cid, quote_info in batch_data.items():
                    quote = quote_info.get('quote', {}).get('USD', {})
                    if cid in detailed_info:
                        detailed_info[cid].update({
                            'price': quote.get('price'),
                            'price_timestamp': quote.get('last_updated'),
                            'market_cap': quote.get('market_cap'),
                            'fully_diluted_market_cap': quote.get('fully_diluted_market_cap'),
                            'market_cap_dominance': quote.get('market_cap_dominance'),
                            'tvl': quote.get('tvl'),
                            'total_supply': quote.get('total_supply'),
                            'circulating_supply': quote.get('circulating_supply'),
                            'max_supply': quote.get('max_supply'),
                            'percent_change_1h': quote.get('percent_change_1h'),
                            'percent_change_24h': quote.get('percent_change_24h'),
                            'percent_change_7d': quote.get('percent_change_7d'),
                            'percent_change_30d': quote.get('percent_change_30d'),
                            'percent_change_60d': quote.get('percent_change_60d'),
                            'percent_change_90d': quote.get('percent_change_90d')
                        })
        time.sleep(0.5)

    logging.info(f"Fetched detailed and price info for {len(detailed_info)} coins.")
    return {"detailed_info": detailed_info, "rank_map": rank_map}

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

@task(outlets=[coin_table_asset])
def cmc_upsert_to_db(fetch_result: dict):
    """
    Upsert detailed CMC info to the database.
    """
    detailed_info = fetch_result.get('detailed_info', {})
    rank_map = fetch_result.get('rank_map', {})
    
    # Load chains configuration
    chains_config = load_chains_config()
    platform_to_chain = {
        c['cmc_platform_name']: c['name'] 
        for c in chains_config['chains']
    }
    
    # Load all coins configured in coin-families.yml to check against
    family_coins = set()
    try:
        import yaml
        ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        families_file = os.path.join(ROOT_DIR, 'include', 'config', 'coin-families.yml')
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

    # Fallback metadata for configuration-based coins
    FALLBACK_METADATA = {
        'ETH': {
            'name': 'Ethereum',
            'slug': 'ethereum',
            'ethereum_address': '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
            'decimals': 18
        },
        'WETH': {
            'name': 'Wrapped Ether',
            'slug': 'wrapped-ether',
            'ethereum_address': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
            'decimals': 18
        },
        'USDC': {
            'name': 'USD Coin',
            'slug': 'usd-coin',
            'ethereum_address': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
            'decimals': 6
        },
        'USDT': {
            'name': 'Tether',
            'slug': 'tether',
            'ethereum_address': '0xdac17f958d2ee523a2206206994597c13d831ec7',
            'decimals': 6
        },
        'DAI': {
            'name': 'Dai',
            'slug': 'dai',
            'ethereum_address': '0x6b175474e89094c44da98b954eedeac495271d0f',
            'decimals': 18
        },
        'WBTC': {
            'name': 'Wrapped Bitcoin',
            'slug': 'wrapped-bitcoin',
            'ethereum_address': '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599',
            'decimals': 8
        },
        'STETH': {
            'name': 'Liquid Staked Ether',
            'slug': 'staked-ether',
            'ethereum_address': '0xae7ab96520de3a18e5e111b5eaab095312d7fe84',
            'decimals': 18
        },
        'WSTETH': {
            'name': 'Wrapped Lido Staked Ether',
            'slug': 'wrapped-steth',
            'ethereum_address': '0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0',
            'decimals': 18
        },
        'RETH': {
            'name': 'Rocket Pool ETH',
            'slug': 'rocket-pool-eth',
            'ethereum_address': '0xae78736cd615f374d3085123a210448e74fc6393',
            'decimals': 18
        },
        'PENDLE': {
            'name': 'Pendle',
            'slug': 'pendle',
            'ethereum_address': '0x808507121b80c0546a1d48931130635e169fa121',
            'decimals': 18
        },
        'ENA': {
            'name': 'Ethena',
            'slug': 'ethena',
            'ethereum_address': '0x57e114b691db790c35207b2e685d4a43181e6061',
            'decimals': 18
        },
        'USDE': {
            'name': 'Ethena USDe',
            'slug': 'ethena-usde',
            'ethereum_address': '0x4c9edd5852cd14fe7183fdb42c274d2808b04a55',
            'decimals': 18
        },
        'SUSDS': {
            'name': 'Savings USDS',
            'slug': 'savings-usds',
            'ethereum_address': '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd',
            'decimals': 18
        }
    }

    # Find family coins that were not fetched from CMC
    fetched_symbols = {info.get('symbol', '').upper() for info in detailed_info.values() if info.get('symbol')}
    missing_family_symbols = family_coins - fetched_symbols
    for sym in missing_family_symbols:
        fallback = FALLBACK_METADATA.get(sym)
        mock_id = f"fallback_{sym.lower()}"
        if fallback:
            detailed_info[mock_id] = {
                'symbol': sym,
                'name': fallback['name'],
                'slug': fallback['slug'],
                'logo': None,
                'contract_address': [{'platform': {'name': 'Ethereum'}, 'contract_address': fallback['ethereum_address']}],
                'decimals': fallback['decimals']
            }

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
    
    # Decimals mapping dictionary for standard overrides
    DECIMALS_MAP = {
        'ETH': 18, 'WETH': 18, 'USDC': 6, 'USDT': 6, 'DAI': 18, 'WBTC': 8, 'EURC': 6,
        'BTC': 8, 'CBBTC': 8, 'TBTC': 18, 'FBTC': 8, 'LBTC': 8, 'KBTC': 8, 'USDE': 18,
        'SUSDE': 18
    }

    # Upsert to DB
    with pg_hook.get_conn() as conn:
        with conn.cursor() as cur:
             cur.execute("SELECT id, name FROM chain")
             chain_map = {row[1].lower(): row[0] for row in cur.fetchall()}
             chain_map['bsc'] = chain_map.get('bnb')
             for cmc_id_str, info in sorted_items:
                try:
                    cmc_id = int(cmc_id_str)
                except ValueError:
                    cmc_id = None
                
                symbol = info.get('symbol', '').upper()
                name = info.get('name')
                slug = info.get('slug')
                logo = info.get('logo')
                rank = rank_map.get(cmc_id_str)
                
                decimals = info.get('decimals', 18)
                contracts = info.get('contract_address', [])
                chain_addresses = {}
                if isinstance(contracts, list):
                    for c in contracts:
                        platform_name = c.get('platform', {}).get('name', '')
                        chain = platform_to_chain.get(platform_name)
                        if chain:
                            chain_addresses[chain] = c.get('contract_address')
                
                # Check decimals override
                if symbol in DECIMALS_MAP:
                    decimals = DECIMALS_MAP[symbol]

                # Sentinel logic for native coins on configured chains
                for c_conf in chains_config['chains']:
                    ch_name = c_conf['name']
                    native_sym = c_conf['native_coin_symbol']
                    if ch_name not in chain_addresses:
                        if symbol == native_sym:
                            if ch_name == 'ethereum':
                                chain_addresses[ch_name] = '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
                            else:
                                chain_addresses[ch_name] = '0x0000000000000000000000000000000000000000'

                # Fallback for family coins that don't have Ethereum addresses on CMC
                if not chain_addresses and symbol in family_coins:
                    fallback = FALLBACK_METADATA.get(symbol)
                    if fallback:
                        chain_addresses['ethereum'] = fallback.get('ethereum_address')
                        decimals = fallback.get('decimals', decimals)
                
                # Only keep coins that have at least one contract address (skip if none)
                if not chain_addresses:
                    continue
                
                if not symbol:
                    continue

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
                    info.get('price'), info.get('price_timestamp'), info.get('market_cap'),
                    info.get('percent_change_1h'), info.get('percent_change_24h'), info.get('percent_change_7d'),
                    info.get('percent_change_30d'), info.get('percent_change_60d'), info.get('percent_change_90d'),
                    info.get('fully_diluted_market_cap'), info.get('market_cap_dominance'), info.get('tvl'),
                    info.get('total_supply'), info.get('circulating_supply'), info.get('max_supply')
                ))
                coin_id = cur.fetchone()[0]
                
                # Upsert contract addresses into coin_contract
                for chain_name, contract_addr in chain_addresses.items():
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
                            INSERT INTO coin_contract (coin_id, chain_id, contract_address, decimals, is_native, verified_at)
                            VALUES (%s, %s, %s, %s, %s, NOW())
                            ON CONFLICT (coin_id, chain_id) DO UPDATE SET
                                contract_address = EXCLUDED.contract_address,
                                decimals = COALESCE(EXCLUDED.decimals, coin_contract.decimals),
                                verified_at = EXCLUDED.verified_at
                        """, (coin_id, chain_id, contract_addr.lower(), ch_decimals, is_native))
                
                upsert_count += 1
        conn.commit()

    Variable.set("CMC_LAST_MAPPING_SYNC", now.to_iso8601_string())
    logging.info(f"✅ CMC mapping sync complete. Upserted {upsert_count} coins.")
    return upsert_count

@task(outlets=[coin_table_asset])
def skip_sync():
    logging.info("⏭️ Mapping is already fresh. Skipping.")

with DAG(
    dag_id='cmc_global_coin_metadata',
    default_args=default_args,
    description='Sync CoinMarketCap coin-to-address mapping',
    schedule='@weekly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['metadata', 'coinmarketcap'],
    params={
        'force_update': Param(False, description='Force mapping refresh even if fresh'),
        'max_rank': Param(1500, description='Maximum CMC rank to fetch metadata for'),
        'max_coins': Param(500, description='Maximum number of coins to sync'),
    },
) as dag:
    
    check = check_mapping_freshness(force_update="{{ params.force_update }}")
    map_fetch = cmc_map_fetch(max_rank="{{ params.max_rank }}", max_coins="{{ params.max_coins }}")
    info_fetch = cmc_info_fetch(map_fetch)
    upsert = cmc_upsert_to_db(info_fetch)
    skip = skip_sync()
    
    check >> [map_fetch, skip]
