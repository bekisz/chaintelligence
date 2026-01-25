from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import sys
import os

from utils.uniswap_utils import UniswapV3Fetcher, PostgresStorage

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2026, 1, 20),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

def fetch_and_store_swaps(**context):
    """
    Fetch swaps incrementally or perform a 90-day backfill if empty.
    """
    fetcher = UniswapV3Fetcher(verbose=True)
    storage = PostgresStorage()
    
    # 1. Determine start date
    last_ts = storage.get_last_swap_timestamp()
    
    if last_ts:
        # Incremental: Start from 1 second after the last record
        start_date = datetime.fromtimestamp(last_ts + 1)
        print(f"Incremental run: Last record at {datetime.fromtimestamp(last_ts)}. Fetching new swaps...")
    else:
        # Backfill: Start from 90 days ago
        start_date = datetime.now() - timedelta(days=90)
        print("Database is empty. Starting 90-day backfill...")
        
    end_date = datetime.now()
    
    print(f"Fetching swaps from {start_date} to {end_date}")
    # Pass storage.save_swaps as a callback to save data batch-by-batch
    swaps = fetcher.fetch_swaps(start_date, end_date, on_batch_callback=storage.save_swaps)
    
    print(f"Fetch complete. Total unique swaps processed: {len(swaps)}")
    print("Successfully synchronized swaps.")

with DAG(
    'the_graph_uniswap_v3_swaps',
    default_args=default_args,
    description='Fetch Uniswap V3 swaps for tracked tokens',
    schedule_interval=timedelta(hours=1),
    catchup=False,
    tags=['uniswap', 'swaps', 'defi'],
) as dag:

    fetch_task = PythonOperator(
        task_id='fetch_and_store_swaps',
        python_callable=fetch_and_store_swaps,
    )
