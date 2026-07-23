from airflow import DAG
from airflow.sdk import task
import pendulum
from datetime import datetime, timedelta, timezone
import logging
from common.utils.uniswap_utils import UniswapV4Fetcher, PostgresStorageV4
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
    'graph_ethereum_uniswap_v4_swaps',
    max_active_runs=1,
    default_args=default_args,
    description='Fetch Uniswap V4 swaps on Ethereum from The Graph',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['uniswap', 'swaps', 'defi', 'v4', 'ethereum'],
) as dag:
    @task
    def fetch_and_store_swaps(**context):
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf
        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('Ethereum', 30)
        force_backfill = 'backfill_days' in conf
        network = 'Ethereum'
        protocol = 'Uniswap V4'
        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            """
            SELECT MAX(s.ts) 
            FROM swaps s
            JOIN liquidity_pool lp ON s.pool_id = lp.id
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            WHERE ch.name = %s AND pr.name = %s
            """,
            parameters=(network, protocol))
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None
        end_date = datetime.now(timezone.utc)
        if last_ts is not None and not force_backfill:
            if last_ts.tzinfo is None: last_ts = last_ts.replace(tzinfo=timezone.utc)
            start_date = last_ts
        else:
            start_date = end_date - timedelta(days=days)
        logging.info(f"Fetching {network} {protocol} swaps from {start_date} to {end_date}")
        fetcher = UniswapV4Fetcher(network=network, protocol=protocol, verbose=True)
        storage = PostgresStorageV4()
        count = fetcher.fetch_swaps(start_date, end_date,
            on_batch_callback=lambda s, net=network: storage.save_swaps(s, network=net, protocol=protocol),
            collect_results=False)
        logging.info(f"{network} {protocol} fetch complete. Processed {count} unique swaps.")
    fetch_and_store_swaps()
