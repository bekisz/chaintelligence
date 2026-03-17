import sys
import os
import logging
from datetime import datetime, timezone, timedelta

os.environ["AIRFLOW_HOME"] = "/opt/airflow"
sys.path.append("/opt/airflow/dags")

from common.utils.uniswap_utils import UniswapV4Fetcher
from airflow.providers.postgres.hooks.postgres import PostgresHook

logging.basicConfig(level=logging.INFO)

pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
conn = pg_hook.get_conn()
cur = conn.cursor()

# Get EURI address
cur.execute("SELECT symbol, ethereum_address FROM coin WHERE symbol='EURI'")
euri_addr = cur.fetchone()[1]
cur.execute("SELECT symbol, ethereum_address FROM coin WHERE symbol='USDC'")
usdc_addr = cur.fetchone()[1]

print(f"EURI: {euri_addr}")
print(f"USDC: {usdc_addr}")

fetcher = UniswapV4Fetcher(verbose=True)
start_date = datetime.now(timezone.utc) - timedelta(days=90)

data = fetcher.fetch_pool_daily_data(euri_addr, usdc_addr, 3000, start_date)
print(f"Returned Data: {data}")
