from airflow import DAG
from airflow.sdk import task, Asset
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param
import pendulum
from datetime import timedelta
import logging
import os
from typing import List, Dict

# Asset for metadata tracking
asset_coin_price_history = Asset("postgres://postgres:5432/chaintelligence/public/coin_price_history")

@task
def sync_coin_families():
    """
    Sync the YAML coin families to the database coin_family table.
    """
    from include.coin_family_resolver import CoinFamilyResolver
    from airflow.providers.postgres.hooks.postgres import PostgresHook
    import os
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn_uri = pg_hook.get_uri()
    config_path = os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'include/config/coin-families.yml')
    
    resolver = CoinFamilyResolver(config_path, conn_uri)
    resolver.sync_to_db()
    logging.info("Successfully synced coin families to database.")

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

@task
def identify_coins_to_sync(target_symbols: list = None) -> List[Dict]:
    """
    Task 1: Identify coins to sync. 
    If target_symbols is provided, sync those (supports Family.*, .* regexp, and specific coins).
    Otherwise, sync Tier 1 (active LP coins).
    """
    from include.coin_family_resolver import CoinFamilyResolver
    from airflow.providers.postgres.hooks.postgres import PostgresHook
    import os
    import json
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    # Get connection string for the resolver
    conn_uri = pg_hook.get_uri()
    config_path = os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'include/config/coin-families.yml')
    
    resolver = CoinFamilyResolver(config_path, conn_uri)
    
    resolved_symbols = []
    
    # Parse inputs (handling string template fallback rendering)
    if isinstance(target_symbols, str):
        target_symbols = target_symbols.strip()
        if target_symbols in ('', '[]', 'None'):
            target_symbols = None
        else:
            try:
                target_symbols = json.loads(target_symbols.replace("'", '"'))
            except Exception:
                target_symbols = [s.strip() for s in target_symbols.split(',')]
    
    if target_symbols and len(target_symbols) > 0:
        logging.info(f"Target symbols/families provided: {target_symbols}. Resolving...")
        
        if not isinstance(target_symbols, list):
            target_symbols = [target_symbols]
            
        resolved_symbols = resolver.resolve_target_symbols(target_symbols)
        logging.info(f"Resolved to {len(resolved_symbols)} symbols: {resolved_symbols[:10]}...")
    else:
        logging.info("No target symbols provided. Identifying Tier 1 coins (active LP positions)...")
        tier1_query = """
            SELECT DISTINCT c.symbol
            FROM coin c
            JOIN (
                SELECT DISTINCT coin0_symbol as symbol FROM liquidity_pool p
                JOIN liquidity_pool_position pos ON p.id = pos.pool_id
                UNION
                SELECT DISTINCT coin1_symbol as symbol FROM liquidity_pool p
                JOIN liquidity_pool_position pos ON p.id = pos.pool_id
            ) active_coins ON c.symbol = active_coins.symbol
            WHERE c.ethereum_address IS NOT NULL;
        """
        resolved_symbols = [r[0] for r in pg_hook.get_records(tier1_query)]
        
    if not resolved_symbols:
        logging.warning("No symbols identified to sync.")
        return []

    # Get addresses for the resolved symbols
    query = "SELECT symbol, ethereum_address FROM coin WHERE UPPER(symbol) = ANY(%s) AND ethereum_address IS NOT NULL"
    coins = pg_hook.get_records(query, parameters=([s.upper() for s in resolved_symbols],))
    
    logging.info(f"Found {len(coins)} coins with valid addresses to sync history for.")
    return [{"symbol": symbol, "address": address} for symbol, address in coins]

@task(outlets=[asset_coin_price_history])
def fetch_and_store_history(coin_list: List[Dict], force_update: bool = False):
    """
    Task 2: Fetch and store historical prices from DeFi Llama for the identified coins.
    """
    from include.defillama_client import fetch_historical_prices
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    # Handle Param boolean conversion
    if isinstance(force_update, str):
        force_update = force_update.lower() in ('true', '1', 'yes')
    
    total_inserted = 0
    
    for coin in coin_list:
        symbol = coin["symbol"]
        address = coin["address"]
        
        logging.info(f"Processing history for {symbol} ({address})")
        
        # Determine sync direction and initial markers
        current_start_ts = None
        current_end_ts = None
        loop_direction = "backward"
        
        if not force_update:
            latest_ts_query = "SELECT MAX(timestamp) FROM coin_price_history WHERE address = %s"
            latest_ts_res = pg_hook.get_first(latest_ts_query, parameters=(address,))
            if latest_ts_res and latest_ts_res[0]:
                current_start_ts = int(latest_ts_res[0].timestamp()) + 1
                loop_direction = "forward"
                logging.info(f"  Existing data found up to {latest_ts_res[0]}. Syncing forward...")
        else:
            logging.info("  Force update enabled. Syncing backward from now...")

        # Batch syncing
        batch_count = 0
        MAX_BATCHES = 10
        
        while batch_count < MAX_BATCHES:
            batch_count += 1
            history = fetch_historical_prices(
                address, 
                start_timestamp=current_start_ts, 
                end_timestamp=current_end_ts,
                points=500
            )
            
            if not history:
                logging.info(f"  No more history found for {symbol}")
                break
                
            logging.info(f"  Batch {batch_count}: Fetched {len(history)} data points")
            
            # Save batch
            conn = pg_hook.get_conn()
            cur = conn.cursor()
            try:
                batch_data = [(address, symbol, pendulum.from_timestamp(p["timestamp"]), p["price"]) for p in history]
                from psycopg2.extras import execute_values
                execute_values(cur, """
                    INSERT INTO coin_price_history (address, symbol, timestamp, price)
                    VALUES %s ON CONFLICT (address, timestamp) DO UPDATE SET price = EXCLUDED.price
                """, batch_data)
                conn.commit()
                total_inserted += len(history)
            except Exception as e:
                logging.error(f"  Failed to save batch for {symbol}: {e}")
                conn.rollback()
                break
            finally:
                cur.close()
                conn.close()

            # Update markers
            if loop_direction == "forward":
                current_start_ts = history[-1]["timestamp"] + 1
            else:
                current_end_ts = history[0]["timestamp"] - 1
            
            if len(history) < 1000:
                break
        
    logging.info(f"✅ History sync complete. Total points processed: {total_inserted}")
    return {"total_coins": len(coin_list), "total_points": total_inserted}

with DAG(
    'coin_price_history_feeder',
    default_args=default_args,
    description='Sync historical coin prices from DeFi Llama (Split Task)',
    schedule='0 1 * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    max_active_runs=1,
    tags=['prices', 'history', 'defillama'],
    params={
        'force_update': Param(
            default=False,
            type='boolean',
            description='Force full re-sync of available history.'
        ),
        'coin_symbols': Param(
            default=[],
            type='array',
            items={'type': 'string'},
            description='Optional: List of coin symbols to sync (e.g. ["ETH", "USDC"]). If empty, syncs all Tier 1 coins.'
        )
    },
) as dag:
    
    sync_families = sync_coin_families()
    
    coins = identify_coins_to_sync(target_symbols="{{ params.coin_symbols }}")
    
    sync_job = fetch_and_store_history(
        coin_list=coins, 
        force_update="{{ params.force_update }}"
    )
    
    sync_families >> coins >> sync_job
