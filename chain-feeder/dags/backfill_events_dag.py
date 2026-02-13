

from airflow import DAG
from datetime import datetime, timedelta
import os
import sys

# Add scripts to path for import
sys.path.append('/opt/airflow/include/scripts')

from airflow.providers.standard.operators.python import PythonOperator
from backfill_position_events import run_backfill

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

def _run_backfill_task(**context):
    conf = context['dag_run'].conf or {}
    start_date = conf.get('from_date')
    
    # Run backfill
    run_backfill(start_date=start_date)

with DAG(
    'backfill_position_events',
    default_args=default_args,
    description='Fetch historical position events (claims, liquidity) from RPC logs',
    schedule='@daily',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['ingestion', 'events', 'rpc'],
) as dag:

    run_backfill_task = PythonOperator(
        task_id='run_backfill_events_script',
        python_callable=_run_backfill_task,
        op_kwargs={},
    )
