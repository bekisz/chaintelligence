from airflow import DAG
from airflow.sdk import task, Asset
import pendulum
from datetime import datetime, timedelta, timezone
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



with DAG(
    'the_graph_uniswap_v3_swaps',
    default_args=default_args,
    description='Fetch Uniswap V3 swaps for tracked tokens (Airflow 3)',
    schedule='@hourly',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['uniswap', 'swaps', 'defi'],
) as dag:

    @task(outlets=[uniswap_v3_swaps_asset])
    def fetch_and_store_ethereum_swaps(**context):
        """Fetch Uniswap V3 swaps for Ethereum network."""
        import pendulum as _pendulum
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf

        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('Ethereum', 30)
        force_backfill = 'backfill_days' in conf  # explicit conf always overrides DB checkpoint
        network = 'Ethereum'

        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            "SELECT MAX(timestamp) FROM uniswap_v3_swaps WHERE network = %s",
            parameters=(network,)
        )
        last_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None

        end_date = datetime.now(timezone.utc)
        if last_ts is not None and not force_backfill:
            # last_ts is a datetime from Postgres; make it timezone-aware if needed
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            start_date = last_ts
        else:
            start_date = end_date - timedelta(days=days)

        logging.info(f"Fetching {network} swaps from {start_date} to {end_date}")
        fetcher = UniswapV3Fetcher(network=network)
        swaps = fetcher.fetch_swaps(start_date=start_date, end_date=end_date)
        logging.info(f"Fetched {len(swaps)} swaps for {network}")
        storage = PostgresStorage()
        storage.save_swaps(swaps, network=network)
        logging.info(f"Saved {len(swaps)} swaps for {network}")

    @task(outlets=[uniswap_v3_swaps_asset])
    def fetch_and_store_arbitrum_swaps(**context):
        """Fetch Uniswap V3 swaps for Arbitrum network."""
        conf = {}
        if context.get('dag_run') and context['dag_run'].conf:
            conf = context['dag_run'].conf

        backfill_days = conf.get('backfill_days', {})
        days = backfill_days.get('Arbitrum', 7)
        force_backfill = 'backfill_days' in conf  # explicit conf always overrides DB checkpoint
        network = 'Arbitrum'

        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        last_ts_row = pg_hook.get_first(
            "SELECT MAX(timestamp) FROM uniswap_v3_swaps WHERE network = %s",
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

        logging.info(f"Fetching {network} swaps from {start_date} to {end_date}")
        fetcher = UniswapV3Fetcher(network=network)
        swaps = fetcher.fetch_swaps(start_date=start_date, end_date=end_date)
        logging.info(f"Fetched {len(swaps)} swaps for {network}")
        storage = PostgresStorage()
        storage.save_swaps(swaps, network=network)
        logging.info(f"Saved {len(swaps)} swaps for {network}")

    ethereum_task = fetch_and_store_ethereum_swaps()
    arbitrum_task = fetch_and_store_arbitrum_swaps()
