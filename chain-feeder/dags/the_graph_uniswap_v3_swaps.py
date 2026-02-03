from airflow import DAG
from airflow.sdk import task, Asset
import pendulum
from datetime import timedelta
import logging

# Use standardized imports from reorganized structure
from common.utils.uniswap_utils import UniswapV3Fetcher, PostgresStorage
from airflow.providers.postgres.hooks.postgres import PostgresHook

# Asset for metadata tracking
uniswap_v3_swaps_asset = Asset("postgres://postgres/chaintelligence/public/uniswap_v3_swaps")

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

@task(outlets=[uniswap_v3_swaps_asset])
def fetch_and_store_swaps():
    """
    Fetch Uniswap V3 swaps incrementally for all tracked tokens.
    Uses PostgresHook to find the last sync point.
    """
    fetcher = UniswapV3Fetcher(verbose=True)
    storage = PostgresStorage() # Still uses internal config but we can bridge it
    
    # Use PostgresHook to get the last sync timestamp
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    try:
        last_ts_query = "SELECT MAX(timestamp) FROM uniswap_v3_swaps"
        res = pg_hook.get_first(last_ts_query)
        last_ts = res[0].timestamp() if res and res[0] else None
    except Exception as e:
        logging.warning(f"Could not fetch last timestamp (might be empty): {e}")
        last_ts = None

    if last_ts:
        # Incremental: Start from 1 second after the last record
        start_date = pendulum.from_timestamp(last_ts + 1)
        logging.info(f"Incremental run: Last record at {pendulum.from_timestamp(last_ts)}. Fetching new swaps...")
    else:
        # Backfill: Start from 90 days ago
        start_date = pendulum.now().subtract(days=90)
        logging.info("Database is empty. Starting 90-day backfill...")
        
    end_date = pendulum.now()
    
    logging.info(f"Fetching swaps from {start_date} to {end_date}")
    
    # We use the existing fetcher and storage logic which works with psycopg2
    # but the DAG wrapper is now clean Airflow 3 Task SDK style
    swaps = fetcher.fetch_swaps(start_date, end_date, on_batch_callback=storage.save_swaps)
    
    logging.info(f"Fetch complete. Total unique swaps processed: {len(swaps)}")
    return f"Successfully synchronized {len(swaps)} swaps."

with DAG(
    'the_graph_uniswap_v3_swaps',
    default_args=default_args,
    description='Fetch Uniswap V3 swaps for tracked tokens (Airflow 3)',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['uniswap', 'swaps', 'defi'],
) as dag:

    fetch_task = fetch_and_store_swaps()
