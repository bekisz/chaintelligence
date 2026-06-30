from airflow import DAG
from airflow.sdk import task, Asset
import pendulum
from datetime import datetime, timedelta, timezone
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

with DAG(
    'the_graph_uniswap_v4_swaps',
    default_args=default_args,
    description='Fetch Uniswap V4 swaps for tracked tokens (Airflow 3)',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['uniswap', 'swaps', 'defi', 'v4'],
) as dag:

    @task(outlets=[uniswap_v4_swaps_asset])
    def fetch_and_store_ethereum_swaps(**context):
        """Fetch Uniswap V4 swaps for Ethereum network."""
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf

        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('Ethereum', 30)
        force_backfill = 'backfill_days' in conf  # explicit conf always overrides DB checkpoint
        network = 'Ethereum'

        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            "SELECT MAX(timestamp) FROM uniswap_v4_swaps WHERE network = %s",
            parameters=(network,)
        )
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None

        end_date = datetime.now(timezone.utc)
        if last_ts is not None and not force_backfill:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            start_date = last_ts
        else:
            start_date = end_date - timedelta(days=days)

        logging.info(f"Fetching {network} V4 swaps from {start_date} to {end_date}")
        fetcher = UniswapV4Fetcher(network=network, verbose=True)
        storage = PostgresStorageV4()
        count = fetcher.fetch_swaps(
            start_date, end_date,
            on_batch_callback=lambda s, net=network: storage.save_swaps(s, network=net),
            collect_results=False
        )
        logging.info(f"{network} V4 fetch complete. Processed {count} unique swaps.")

    @task(outlets=[uniswap_v4_swaps_asset])
    def fetch_and_store_arbitrum_swaps(**context):
        """Fetch Uniswap V4 swaps for Arbitrum network."""
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf

        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('Arbitrum', 7)
        force_backfill = 'backfill_days' in conf  # explicit conf always overrides DB checkpoint
        network = 'Arbitrum'

        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            "SELECT MAX(timestamp) FROM uniswap_v4_swaps WHERE network = %s",
            parameters=(network,)
        )
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None

        end_date = datetime.now(timezone.utc)
        if last_ts is not None and not force_backfill:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            start_date = last_ts
        else:
            start_date = end_date - timedelta(days=days)

        logging.info(f"Fetching {network} V4 swaps from {start_date} to {end_date}")
        fetcher = UniswapV4Fetcher(network=network, verbose=True)
        storage = PostgresStorageV4()
        count = fetcher.fetch_swaps(
            start_date, end_date,
            on_batch_callback=lambda s, net=network: storage.save_swaps(s, network=net),
            collect_results=False
        )
        logging.info(f"{network} V4 fetch complete. Processed {count} unique swaps.")

    @task(outlets=[uniswap_v4_swaps_asset])
    def fetch_and_store_base_swaps(**context):
        """Fetch Uniswap V4 swaps for Base network."""
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf

        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('Base', 7)
        force_backfill = 'backfill_days' in conf
        network = 'Base'

        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            "SELECT MAX(timestamp) FROM uniswap_v4_swaps WHERE network = %s",
            parameters=(network,)
        )
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None

        end_date = datetime.now(timezone.utc)
        if last_ts is not None and not force_backfill:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            start_date = last_ts
        else:
            start_date = end_date - timedelta(days=days)

        logging.info(f"Fetching {network} V4 swaps from {start_date} to {end_date}")
        fetcher = UniswapV4Fetcher(network=network, verbose=True)
        storage = PostgresStorageV4()
        count = fetcher.fetch_swaps(
            start_date, end_date,
            on_batch_callback=lambda s, net=network: storage.save_swaps(s, network=net),
            collect_results=False
        )
        logging.info(f"{network} V4 fetch complete. Processed {count} unique swaps.")

    @task(outlets=[uniswap_v4_swaps_asset])
    def fetch_and_store_bnb_swaps(**context):
        """Fetch Uniswap V4 swaps for BNB network."""
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf

        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('BNB', 7)
        force_backfill = 'backfill_days' in conf
        network = 'BNB'

        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            "SELECT MAX(timestamp) FROM uniswap_v4_swaps WHERE network = %s",
            parameters=(network,)
        )
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None

        end_date = datetime.now(timezone.utc)
        if last_ts is not None and not force_backfill:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            start_date = last_ts
        else:
            start_date = end_date - timedelta(days=days)

        logging.info(f"Fetching {network} V4 swaps from {start_date} to {end_date}")
        fetcher = UniswapV4Fetcher(network=network, verbose=True)
        storage = PostgresStorageV4()
        count = fetcher.fetch_swaps(
            start_date, end_date,
            on_batch_callback=lambda s, net=network: storage.save_swaps(s, network=net),
            collect_results=False
        )
        logging.info(f"{network} V4 fetch complete. Processed {count} unique swaps.")

    ethereum_task = fetch_and_store_ethereum_swaps()
    arbitrum_task = fetch_and_store_arbitrum_swaps()
    base_task = fetch_and_store_base_swaps()
    bnb_task = fetch_and_store_bnb_swaps()
