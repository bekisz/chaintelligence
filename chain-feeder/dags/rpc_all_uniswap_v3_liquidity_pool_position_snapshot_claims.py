from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta
import os

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    max_active_runs=1,
    'rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims',
    default_args=default_args,
    description='Fetch historical claim events from RPC logs and update snapshots',
    schedule='@daily',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['ingestion', 'claims', 'rpc'],
) as dag:

    run_backfill = BashOperator(
        task_id='run_backfill_claims_script',
        bash_command='python /opt/airflow/include/scripts/fetch_claim_history.py',
        env={
            'PYTHONPATH': '/opt/airflow/dags:/opt/airflow/include',
            'DATA_WAREHOUSE_DB': os.getenv('DATA_WAREHOUSE_DB', 'postgresql://airflow:airflow@postgres/chaintelligence'),
            'RPC_URL': os.getenv('RPC_URL', '')
        }
    )
