import sys
import os
import logging

os.environ["AIRFLOW_HOME"] = "/opt/airflow"
sys.path.append("/opt/airflow/dags")

from common.utils.config import ADDRESS_TO_SYMBOL, TOKEN_ADDRESSES

from common.utils.uniswap_utils import UniswapV4Fetcher
fetcher = UniswapV4Fetcher(verbose=True)

from airflow.providers.postgres.hooks.postgres import PostgresHook
pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
conn = pg_hook.get_conn()
cur = conn.cursor()

# Get EURI address from coin table
cur.execute("SELECT symbol, ethereum_address FROM coin WHERE symbol='EURI'")
print(cur.fetchall())
