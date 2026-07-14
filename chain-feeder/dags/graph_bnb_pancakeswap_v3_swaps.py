from airflow import DAG
from airflow.sdk import task
import pendulum
from datetime import datetime, timedelta, timezone
import logging
from common.utils.uniswap_utils import UniswapV3Fetcher, PostgresStorage
from airflow.providers.postgres.hooks.postgres import PostgresHook

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'graph_bnb_pancakeswap_v3_swaps',
    default_args=default_args,
    description='Fetch PancakeSwap V3 swaps on BNB from The Graph',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['pancakeswap', 'swaps', 'defi', 'v3', 'bnb'],
) as dag:
    @task
    def fetch_and_store_swaps(**context):
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf
        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('BNB_PancakeSwap_V3', 7)
        force_backfill = 'backfill_days' in conf
        network = 'BNB'
        protocol = 'PancakeSwap V3'
        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            "SELECT MAX(ts) FROM swaps WHERE network = %s AND protocol = %s",
            parameters=(network, protocol))
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None
        end_date = datetime.now(timezone.utc)
        if last_ts is not None and not force_backfill:
            if last_ts.tzinfo is None: last_ts = last_ts.replace(tzinfo=timezone.utc)
            start_date = last_ts
        else:
            start_date = end_date - timedelta(days=days)
        logging.info(f"Fetching {network} {protocol} swaps from {start_date} to {end_date}")
        fetcher = UniswapV3Fetcher(network=network, protocol=protocol)
        storage = PostgresStorage()
        def save_batch(batch): storage.save_swaps(batch, network=network, protocol=protocol)
        num_swaps = fetcher.fetch_swaps(start_date=start_date, end_date=end_date, on_batch_callback=save_batch, collect_results=False)
        logging.info(f"Fetched and saved {num_swaps} unique swaps for {network} {protocol}")
    fetch_and_store_swaps()
