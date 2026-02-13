from airflow import DAG
from airflow.sdk import task, Asset
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.models.param import Param
from airflow.utils.trigger_rule import TriggerRule
import pendulum
from datetime import timedelta
import logging
import requests
import os

# Import the CoinMarketCap client
from include.coinmarketcap_client import fetch_crypto_prices, fetch_crypto_history

# CMC API Configuration
CMC_API_KEY = os.getenv('CMC_API_KEY', 'ee501995-c447-4274-96a9-cbb7bf06e6bc')
CMC_MAP_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/map"
CMC_INFO_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info"

# Assets (same as CryptoCompare DAG)
asset_coin_prices = Asset("postgres://postgres:5432/chaintelligence/public/coin")
asset_coin_history = Asset("postgres://postgres:5432/chaintelligence/public/coin_price_history")

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=3),
}

# Symbol mapping (same as CryptoCompare)
SYMBOL_MAPPING = {
    'WETH': 'ETH',
    'WBTC': 'BTC',
    'WSTETH': 'ETH',
    'RETH': 'ETH',
    'CBETH': 'ETH',
    'SAVINGS USDS': 'USDS',
    'SUSDS': 'USDS',
}

@task.branch
def check_mapping_freshness(force_mapping: bool = False):
    """
    Decision task: Check if CMC coin mapping needs refresh.
    
    Returns the task ID to execute next:
    - 'sync_cmc_mapping' if refresh needed
    - 'update_coin_prices_cmc' if mapping is fresh (skip sync)
    
    Mapping refresh is needed when:
    1. force_mapping parameter is True, OR
    2. Last mapping sync was over 1 week ago (cmc_last_updated)
    
    Args:
        force_mapping: If True, force mapping sync regardless of freshness
        
    Returns:
        str: Next task ID to execute
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    # Handle parameter (may come as string from Jinja template)
    if isinstance(force_mapping, str):
        force_mapping = force_mapping.lower() in ('true', '1', 'yes')
    
    should_sync = False
    reason = None
    
    if force_mapping:
        should_sync = True
        reason = "Force mapping parameter enabled"
        logging.info("🔄 Force mapping flag is set - will sync")
    else:
        # Check last sync timestamp
        last_sync_query = "SELECT MAX(cmc_last_updated) FROM coin WHERE cmc_last_updated IS NOT NULL"
        last_sync_result = pg_hook.get_first(last_sync_query)
        last_sync = last_sync_result[0] if last_sync_result and last_sync_result[0] else None
        
        if last_sync is None:
            should_sync = True
            reason = "No previous mapping sync found"
            logging.info("⚠️  No previous sync timestamp found - will sync")
        else:
            # Convert to pendulum for comparison
            if isinstance(last_sync, str):
                last_sync = pendulum.parse(last_sync)
            elif not hasattr(last_sync, 'diff'):
                last_sync = pendulum.instance(last_sync)
            
            age_days = pendulum.now('UTC').diff(last_sync).in_days()
            logging.info(f"📅 Last mapping sync was {age_days} days ago ({last_sync})")
            
            if age_days > 7:
                should_sync = True
                reason = f"Last sync was {age_days} days ago (> 7 days)"
                logging.info(f"⏰ Mapping is stale ({age_days} days old) - will sync")
    
    if should_sync:
        logging.info(f"✅ Decision: SYNC MAPPING - Reason: {reason}")
        return 'sync_cmc_mapping'
    else:
        logging.info("✅ Decision: SKIP SYNC - Mapping is fresh and complete")
        # Return all 3 tier checks so they all run in parallel
        return ['check_tier1_needed', 'check_tier2_needed', 'check_tier3_needed']


@task(outlets=[asset_coin_prices])
def sync_cmc_mapping():
    """
    Sync CMC coin mapping data (only runs if check_mapping_freshness decides it's needed).
    
    Fetches:
    - Coin metadata from CoinMarketCap API
    - Logos, ranks, decimals, ethereum addresses
    
    Updates:
    - coin table with metadata and cmc_last_updated timestamp
        
    Returns:
        dict with 'coins_updated' count
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    logging.info("🔄 Starting CMC mapping sync...")
    
    headers = {
        'X-CMC_PRO_API_KEY': CMC_API_KEY,
        'Accept': 'application/json'
    }
    
    # Fetch coin map
    logging.info(f"📡 Fetching CMC map from {CMC_MAP_URL}")
    response = requests.get(CMC_MAP_URL, headers=headers, timeout=30)
    response.raise_for_status()
    
    data = response.json()
    all_coins = data.get('data', [])
    logging.info(f"📥 Received {len(all_coins)} coins from CMC")

    # Filter for active Ethereum tokens and major coins
    # Also store rank from the map endpoint (info endpoint doesn't include rank)
    target_ids = []
    rank_map = {}  # Store rank by cmc_id
    
    for coin in all_coins:
        is_active = coin.get('is_active')
        symbol = coin.get('symbol')
        cmc_id = coin.get('id')
        rank = coin.get('rank')  # Get rank from /map endpoint
        platform = coin.get('platform')
        
        if not is_active:
            continue
        
        is_target = False
        if platform and platform.get('slug') == 'ethereum':
            is_target = True
        elif symbol in ['ETH', 'BTC', 'WBTC']:
            is_target = True
            
        if is_target:
            target_ids.append(str(cmc_id))
            if rank is not None:
                rank_map[str(cmc_id)] = rank

    logging.info(f"🎯 Identified {len(target_ids)} active Ethereum-related coins for metadata fetch")

    # Batch fetch detailed info
    detailed_info = {}
    
    for i in range(0, len(target_ids), 100):
        batch = target_ids[i:i+100]
        logging.info(f"⬇️  Fetching metadata batch {i//100 + 1}/{(len(target_ids)-1)//100 + 1}")
        res = requests.get(CMC_INFO_URL, headers=headers, params={'id': ','.join(batch)}, timeout=30)
        if res.status_code == 200:
            batch_data = res.json().get('data', {})
            detailed_info.update(batch_data)
        else:
            logging.error(f"❌ Failed to fetch metadata batch: {res.text}")

    # Upsert to database
    upsert_count = 0
    now = pendulum.now('UTC')
    
    with pg_hook.get_conn() as conn:
        with conn.cursor() as cur:
            for cmc_id_str, info in detailed_info.items():
                cmc_id = int(cmc_id_str)
                symbol = info.get('symbol', '').upper()
                name = info.get('name')
                slug = info.get('slug')
                logo = info.get('logo')
                
                # Get rank from the map endpoint (stored earlier)
                # The /info endpoint doesn't include rank, but /map does
                rank = rank_map.get(cmc_id_str)
                
                # Extract Ethereum address
                eth_address = None
                decimals = 18
                
                contracts = info.get('contract_address', [])
                if isinstance(contracts, list):
                    for c in contracts:
                        plt = c.get('platform', {})
                        if plt.get('name') == 'Ethereum' or plt.get('coin', {}).get('slug') == 'ethereum':
                            eth_address = c.get('contract_address')
                            break
                
                # Manual decimal overrides
                if symbol == 'ETH': 
                    decimals = 18
                elif symbol in ['BTC', 'WBTC']: 
                    decimals = 8
                elif symbol in ['USDC', 'USDT', 'EURC']: 
                    decimals = 6
                
                if not symbol:
                    continue

                cur.execute("""
                    INSERT INTO coin (
                        symbol, name, slug, cmc_id, cmc_rank, 
                        ethereum_address, image_url, decimals, cmc_last_updated
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name = EXCLUDED.name,
                        slug = EXCLUDED.slug,
                        cmc_id = EXCLUDED.cmc_id,
                        cmc_rank = EXCLUDED.cmc_rank,
                        ethereum_address = EXCLUDED.ethereum_address,
                        image_url = EXCLUDED.image_url,
                        cmc_last_updated = EXCLUDED.cmc_last_updated,
                        decimals = CASE 
                            WHEN EXCLUDED.decimals != 18 THEN EXCLUDED.decimals 
                            ELSE coin.decimals 
                        END;
                """, (
                    symbol[:8], name, slug, cmc_id, rank, 
                    eth_address, logo, decimals, now
                ))
                upsert_count += 1
                
        conn.commit()
        
    logging.info(f"✅ Successfully synced metadata for {upsert_count} coins")
    return {'coins_updated': upsert_count}



@task.branch(trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)
def check_tier1_needed(force_update: bool = False):
    """
    Check if Tier 1 (active LP coins) needs updating.
    Always returns the update task since tier 1 always runs (unless no active LPs).
    
    Args:
        force_update: If True, bypass checks and force update
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    tier1_query = """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT pool.coin0_symbol
            FROM liquidity_pool_position pos
            JOIN liquidity_pool pool ON pos.pool_id = pool.id
            UNION
            SELECT DISTINCT pool.coin1_symbol
            FROM liquidity_pool_position pos
            JOIN liquidity_pool pool ON pos.pool_id = pool.id
        ) coins
    """
    count = pg_hook.get_first(tier1_query)[0]
    
    # Handle Jinja template string → boolean
    if isinstance(force_update, str):
        force_update = force_update.lower() in ('true', '1', 'yes')
    
    if force_update:
        logging.info(f"🚀 Tier 1: FORCED update for {count} active LP coins")
        return 'update_tier1_prices' if count > 0 else None
    
    logging.info(f"🔍 Tier 1 Check: {count} active LP coins")
    
    if count > 0:
        logging.info(f"✅ Tier 1: Will update {count} coins (always runs)")
        return 'update_tier1_prices'
    else:
        logging.info("⏭️  Tier 1: No active LPs - skipping")
        return None


@task(outlets=[asset_coin_prices])
def update_tier1_prices():
    """
    Tier 1: Update prices for coins in active LP positions (ALWAYS).
    
    These are the most critical prices as they affect live position valuations.
    Runs on every DAG execution.
    """
    from include.coinmarketcap_client import fetch_crypto_prices
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    now = pendulum.now()
    
    # Get coins from active LP positions
    tier1_query = """
        SELECT DISTINCT pool.coin0_symbol as symbol
        FROM liquidity_pool_position pos
        JOIN liquidity_pool pool ON pos.pool_id = pool.id
        UNION
        SELECT DISTINCT pool.coin1_symbol as symbol
        FROM liquidity_pool_position pos
        JOIN liquidity_pool pool ON pos.pool_id = pool.id
    """
    tier1_rows = pg_hook.get_records(tier1_query)
    tier1_symbols = [row[0] for row in tier1_rows if row[0]]
    
    logging.info(f"📊 Tier 1: Updating prices for {len(tier1_symbols)} active LP coins")
    
    # Map symbols and fetch prices
    fetch_symbols = [SYMBOL_MAPPING.get(sym.upper(), sym.upper()) for sym in tier1_symbols]
    all_metrics = fetch_crypto_prices(list(set(fetch_symbols)))
    
    if not all_metrics:
        logging.error("❌ Failed to fetch Tier 1 prices")
        return {'updated': 0, 'tier': 1, 'error': 'API failed'}
    
    # Update database
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    updated_count = 0
    
    for sym in tier1_symbols:
        fetch_sym = SYMBOL_MAPPING.get(sym.upper(), sym.upper())
        metrics = all_metrics.get(fetch_sym)
        
        if metrics and metrics.get('price') is not None:
            try:
                cur.execute("""
                    UPDATE coin 
                    SET price = %s, price_timestamp = %s,
                        percent_change_1h = %s, percent_change_24h = %s, percent_change_7d = %s,
                        percent_change_30d = %s, percent_change_60d = %s, percent_change_90d = %s,
                        market_cap = %s, market_cap_dominance = %s, fully_diluted_market_cap = %s,
                        tvl = %s, total_supply = %s, circulating_supply = %s, max_supply = %s,
                        cmc_last_updated = %s
                    WHERE symbol = %s
                """, (
                    metrics.get('price'), now,
                    metrics.get('percent_change_1h'), metrics.get('percent_change_24h'), metrics.get('percent_change_7d'),
                    metrics.get('percent_change_30d'), metrics.get('percent_change_60d'), metrics.get('percent_change_90d'),
                    metrics.get('market_cap'), metrics.get('market_cap_dominance'), metrics.get('fully_diluted_market_cap'),
                    metrics.get('tvl'), metrics.get('total_supply'), metrics.get('circulating_supply'), metrics.get('max_supply'),
                    metrics.get('last_updated'), sym
                ))
                updated_count += 1
            except Exception as e:
                logging.error(f"Failed to update price for {sym}: {e}")
                conn.rollback()
    
    conn.commit()
    
    # Daily snapshot
    today = pendulum.now().start_of('day')
    try:
        cur.execute("""
            INSERT INTO coin_price_history (symbol, timestamp, price)
            SELECT symbol, %s, price FROM coin WHERE price IS NOT NULL
            ON CONFLICT (symbol, timestamp) DO UPDATE SET price = EXCLUDED.price
        """, (today,))
        conn.commit()
    except Exception as e:
        logging.error(f"Failed to record daily snapshots: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
    
    logging.info(f"✅ Tier 1: Updated {updated_count}/{len(tier1_symbols)} coins")
    return {'updated': updated_count, 'tier': 1, 'total': len(tier1_symbols)}


@task.branch(trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)
def check_tier2_needed(force_update: bool = False):
    """
    Check if Tier 2 (top 200) needs updating.
    Only returns update task if prices are stale or force_update is True.
    
    Args:
        force_update: If True, bypass staleness check and force update
    """
    import os
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    now = pendulum.now()
    
    # Handle Jinja template string → boolean
    if isinstance(force_update, str):
        force_update = force_update.lower() in ('true', '1', 'yes')
    
    if force_update:
        # Get total count of top 200 coins
        count_query = "SELECT COUNT(*) FROM coin WHERE cmc_rank IS NOT NULL AND cmc_rank <= 200"
        count = pg_hook.get_first(count_query)[0]
        logging.info(f"🚀 Tier 2: FORCED update for {count} top 200 coins")
        return 'update_tier2_prices' if count > 0 else None
    
    TIER2_INTERVAL_MINUTES = int(os.getenv('CMC_TIER2_INTERVAL_MINUTES', '30'))
    tier2_cutoff = now.subtract(minutes=TIER2_INTERVAL_MINUTES)
    
    tier2_query = """
        SELECT COUNT(*) FROM coin 
        WHERE cmc_rank IS NOT NULL AND cmc_rank <= 200
        AND (price_timestamp IS NULL OR price_timestamp < %s)
    """
    count = pg_hook.get_first(tier2_query, parameters=(tier2_cutoff,))[0]
    
    logging.info(f"🔍 Tier 2 Check: {count} coins need update (>{TIER2_INTERVAL_MINUTES} min stale)")
    
    if count > 0:
        logging.info(f"✅ Tier 2: Will update {count} coins")
        return 'update_tier2_prices'
    else:
        logging.info("⏭️  Tier 2: All prices fresh - skipping")
        return None


@task(outlets=[asset_coin_prices])
def update_tier2_prices():
    """
    Tier 2: Update prices for top 200 coins (CONDITIONAL).
    
    Only updates if last price is older than CMC_TIER2_INTERVAL_MINUTES (default: 30 min).
    """
    import os
    from include.coinmarketcap_client import fetch_crypto_prices
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    now = pendulum.now()
    
    TIER2_INTERVAL_MINUTES = int(os.getenv('CMC_TIER2_INTERVAL_MINUTES', '30'))
    tier2_cutoff = now.subtract(minutes=TIER2_INTERVAL_MINUTES)
    
    tier2_query = """
        SELECT symbol FROM coin 
        WHERE cmc_rank IS NOT NULL AND cmc_rank <= 200
        AND (price_timestamp IS NULL OR price_timestamp < %s)
    """
    tier2_rows = pg_hook.get_records(tier2_query, parameters=(tier2_cutoff,))
    tier2_symbols = [row[0] for row in tier2_rows if row[0]]
    
    logging.info(f"📊 Tier 2: Updating prices for {len(tier2_symbols)} top 200 coins")
    
    # Map symbols and fetch prices
    fetch_symbols = [SYMBOL_MAPPING.get(sym.upper(), sym.upper()) for sym in tier2_symbols]
    all_metrics = fetch_crypto_prices(list(set(fetch_symbols)))
    
    if not all_metrics:
        logging.error("❌ Failed to fetch Tier 2 prices")
        return {'updated': 0, 'tier': 2, 'error': 'API failed'}
    
    # Update database  
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    updated_count = 0
    
    for sym in tier2_symbols:
        fetch_sym = SYMBOL_MAPPING.get(sym.upper(), sym.upper())
        metrics = all_metrics.get(fetch_sym)
        
        if metrics and metrics.get('price') is not None:
            try:
                cur.execute("""
                    UPDATE coin 
                    SET price = %s, price_timestamp = %s,
                        percent_change_1h = %s, percent_change_24h = %s, percent_change_7d = %s,
                        percent_change_30d = %s, percent_change_60d = %s, percent_change_90d = %s,
                        market_cap = %s, market_cap_dominance = %s, fully_diluted_market_cap = %s,
                        tvl = %s, total_supply = %s, circulating_supply = %s, max_supply = %s,
                        cmc_last_updated = %s
                    WHERE symbol = %s
                """, (
                    metrics.get('price'), now,
                    metrics.get('percent_change_1h'), metrics.get('percent_change_24h'), metrics.get('percent_change_7d'),
                    metrics.get('percent_change_30d'), metrics.get('percent_change_60d'), metrics.get('percent_change_90d'),
                    metrics.get('market_cap'), metrics.get('market_cap_dominance'), metrics.get('fully_diluted_market_cap'),
                    metrics.get('tvl'), metrics.get('total_supply'), metrics.get('circulating_supply'), metrics.get('max_supply'),
                    metrics.get('last_updated'), sym
                ))
                updated_count += 1
            except Exception as e:
                logging.error(f"Failed to update price for {sym}: {e}")
                conn.rollback()
    
    conn.commit()
    cur.close()
    conn.close()
    
    logging.info(f"✅ Tier 2: Updated {updated_count}/{len(tier2_symbols)} coins")
    return {'updated': updated_count, 'tier': 2, 'total': len(tier2_symbols)}


@task.branch(trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)
def check_tier3_needed(force_update: bool = False):
    """
    Check if Tier 3 (rank 200-500) needs updating.
    Only returns update task if prices are stale or force_update is True.
    
    Args:
        force_update: If True, bypass staleness check and force update
    """
    import os
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    now = pendulum.now()
    
    # Handle Jinja template string → boolean
    if isinstance(force_update, str):
        force_update = force_update.lower() in ('true', '1', 'yes')
    
    if force_update:
        # Get total count of rank 200-500 coins
        count_query = "SELECT COUNT(*) FROM coin WHERE cmc_rank IS NOT NULL AND cmc_rank > 200 AND cmc_rank <= 500"
        count = pg_hook.get_first(count_query)[0]
        logging.info(f"🚀 Tier 3: FORCED update for {count} rank 200-500 coins")
        return 'update_tier3_prices' if count > 0 else None
    
    TIER3_INTERVAL_MINUTES = int(os.getenv('CMC_TIER3_INTERVAL_MINUTES', '60'))
    tier3_cutoff = now.subtract(minutes=TIER3_INTERVAL_MINUTES)
    
    tier3_query = """
        SELECT COUNT(*) FROM coin 
        WHERE cmc_rank IS NOT NULL AND cmc_rank > 200 AND cmc_rank <= 500
        AND (price_timestamp IS NULL OR price_timestamp < %s)
    """
    count = pg_hook.get_first(tier3_query, parameters=(tier3_cutoff,))[0]
    
    logging.info(f"🔍 Tier 3 Check: {count} coins need update (>{TIER3_INTERVAL_MINUTES} min stale)")
    
    if count > 0:
        logging.info(f"✅ Tier 3: Will update {count} coins")
        return 'update_tier3_prices'
    else:
        logging.info("⏭️  Tier 3: All prices fresh - skipping")
        return None


@task(outlets=[asset_coin_prices])
def update_tier3_prices():
    """
    Tier 3: Update prices for rank 200-500 coins (CONDITIONAL).
    
    Only updates if last price is older than CMC_TIER3_INTERVAL_MINUTES (default: 60 min).
    """
    import os
    from include.coinmarketcap_client import fetch_crypto_prices
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    now = pendulum.now()
    
    TIER3_INTERVAL_MINUTES = int(os.getenv('CMC_TIER3_INTERVAL_MINUTES', '60'))
    tier3_cutoff = now.subtract(minutes=TIER3_INTERVAL_MINUTES)
    
    tier3_query = """
        SELECT symbol FROM coin 
        WHERE cmc_rank IS NOT NULL AND cmc_rank > 200 AND cmc_rank <= 500
        AND (price_timestamp IS NULL OR price_timestamp < %s)
    """
    tier3_rows = pg_hook.get_records(tier3_query, parameters=(tier3_cutoff,))
    tier3_symbols = [row[0] for row in tier3_rows if row[0]]
    
    logging.info(f"📊 Tier 3: Updating prices for {len(tier3_symbols)} rank 200-500 coins")
    
    # Map symbols and fetch prices
    fetch_symbols = [SYMBOL_MAPPING.get(sym.upper(), sym.upper()) for sym in tier3_symbols]
    all_metrics = fetch_crypto_prices(list(set(fetch_symbols)))
    
    if not all_metrics:
        logging.error("❌ Failed to fetch Tier 3 prices")
        return {'updated': 0, 'tier': 3, 'error': 'API failed'}
    
    # Update database
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    updated_count = 0
    
    for sym in tier3_symbols:
        fetch_sym = SYMBOL_MAPPING.get(sym.upper(), sym.upper())
        metrics = all_metrics.get(fetch_sym)
        
        if metrics and metrics.get('price') is not None:
            try:
                cur.execute("""
                    UPDATE coin 
                    SET price = %s, price_timestamp = %s,
                        percent_change_1h = %s, percent_change_24h = %s, percent_change_7d = %s,
                        percent_change_30d = %s, percent_change_60d = %s, percent_change_90d = %s,
                        market_cap = %s, market_cap_dominance = %s, fully_diluted_market_cap = %s,
                        tvl = %s, total_supply = %s, circulating_supply = %s, max_supply = %s,
                        cmc_last_updated = %s
                    WHERE symbol = %s
                """, (
                    metrics.get('price'), now,
                    metrics.get('percent_change_1h'), metrics.get('percent_change_24h'), metrics.get('percent_change_7d'),
                    metrics.get('percent_change_30d'), metrics.get('percent_change_60d'), metrics.get('percent_change_90d'),
                    metrics.get('market_cap'), metrics.get('market_cap_dominance'), metrics.get('fully_diluted_market_cap'),
                    metrics.get('tvl'), metrics.get('total_supply'), metrics.get('circulating_supply'), metrics.get('max_supply'),
                    metrics.get('last_updated'), sym
                ))
                updated_count += 1
            except Exception as e:
                logging.error(f"Failed to update price for {sym}: {e}")
                conn.rollback()
    
    conn.commit()
    cur.close()
    conn.close()
    
    logging.info(f"✅ Tier 3: Updated {updated_count}/{len(tier3_symbols)} coins")
    return {'updated': updated_count, 'tier': 3, 'total': len(tier3_symbols)}


with DAG(
    'coin_price_update_cmc',
    default_args=default_args,
    description='Update coin prices every 15 minutes using CoinMarketCap (with auto-sync mapping)',
    schedule='*/15 * * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['prices', 'coinmarketcap', 'maintenance'],
    params={
        'force_cmc_mapping': Param(
            default=False,
            type='boolean',
            description='Force CMC mapping sync regardless of freshness. '
                        'Auto-sync occurs when: last refresh > 7 days OR missing top 1000 coins.'
        ),
        'force_update_all': Param(
            default=False,
            type='boolean',
            description='Force price updates for all 3 tiers regardless of staleness.'
        )
    },
) as dag:
    """
    CoinMarketCap Price Update DAG with Integrated Mapping Sync
    
    This DAG performs two main functions:
    
    1. **Mapping Sync (Auto or Forced)**:
       - Automatically syncs CMC coin mapping when:
         * Last sync was over 1 week ago (checks cmc_last_updated)
         * Missing coins from top 1000 rankings (cmc_rank <= 1000)
       - Can be forced via DAG parameter: force_cmc_mapping=True
       - Updates: cmc_id, cmc_rank, name, slug, logo, ethereum_address, decimals
    
    2. **Price Updates (3-Tier Strategy)**:
       - Tier 1 (Always): Coins in active LP positions (~every 15 min)
       - Tier 2 (Conditional): Top 200 coins (every 30 min, configurable)
       - Tier 3 (Conditional): Rank 200-500 (hourly, configurable)
       
    Parameters:
        force_cmc_mapping (bool): Force mapping sync. Default: False
        
    Environment Variables:
        CMC_API_KEY: CoinMarketCap API key
        CMC_TIER2_INTERVAL_MINUTES: Interval for top 200 coins (default: 30)
        CMC_TIER3_INTERVAL_MINUTES: Interval for rank 200-500 (default: 60)
    """
    
    # Decision: Check if mapping needs refresh
    mapping_check = check_mapping_freshness(
        force_mapping="{{ params.force_cmc_mapping }}"
    )
    
    # Conditional: Sync mapping if check decides it's needed
    mapping_sync_task = sync_cmc_mapping()
    
    # 3 Parallel branches: Check if each tier needs updating
    tier1_check = check_tier1_needed(force_update="{{ params.force_update_all }}")
    tier2_check = check_tier2_needed(force_update="{{ params.force_update_all }}")
    tier3_check = check_tier3_needed(force_update="{{ params.force_update_all }}")
    
    # Tier update tasks (only run if check decides they're needed)
    tier1_update = update_tier1_prices()
    tier2_update = update_tier2_prices()
    tier3_update = update_tier3_prices()
    
    # Set dependencies:
    #  1. Mapping check → sync or skip to tier checks
    #  2. All 3 tier checks run in parallel after mapping
    #  3. Each check → its corresponding update task (or skip)
    mapping_check >> [mapping_sync_task, tier1_check, tier2_check, tier3_check]
    mapping_sync_task >> [tier1_check, tier2_check, tier3_check]
    
    tier1_check >> tier1_update
    tier2_check >> tier2_update
    tier3_check >> tier3_update

