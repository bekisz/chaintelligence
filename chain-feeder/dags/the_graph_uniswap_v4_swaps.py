from airflow import DAG
from airflow.sdk import task, Asset
import pendulum
from datetime import timedelta
import logging

# Use standardized imports from reorganized structure
from common.utils.uniswap_utils import UniswapV4Fetcher, PostgresStorageV4
from airflow.providers.postgres.hooks.postgres import PostgresHook

# Asset for metadata tracking
uniswap_v4_swaps_asset = Asset("postgres://postgres/chaintelligence/public/uniswap_v4_swaps")

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

@task(outlets=[uniswap_v4_swaps_asset])
def fetch_and_store_swaps():
    """
    Fetch Uniswap V4 swaps incrementally for all tracked tokens.
    Uses PostgresHook to find the last sync point.
    """
    fetcher = UniswapV4Fetcher(verbose=True)
    storage = PostgresStorageV4()
    
    # Use PostgresHook to get the last sync timestamp
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    try:
        last_ts_query = "SELECT MAX(timestamp) FROM uniswap_v4_swaps"
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
    
    # Set collect_results=False to avoid OOM for large backfills (only return count)
    count = fetcher.fetch_swaps(start_date, end_date, on_batch_callback=storage.save_swaps, collect_results=False)
    
    logging.info(f"Fetch complete. Total unique swaps processed: {count}")
    return f"Successfully synchronized {count} swaps."

with DAG(
    'the_graph_uniswap_v4_swaps',
    default_args=default_args,
    description='Fetch Uniswap V4 swaps for tracked tokens (Airflow 3)',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['uniswap', 'swaps', 'defi', 'v4'],
) as dag:

    fetch_task = fetch_and_store_swaps()
