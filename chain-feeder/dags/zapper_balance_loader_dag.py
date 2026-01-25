from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
from datetime import timedelta
import logging
import psycopg2
import json

from zapper_client import fetch_zapper_data

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

DB_CONN = "dbname=chaintelligence user=airflow password=airflow host=postgres port=5432"

def etl_process(**kwargs):
    # 1. Fetch
    logging.info("Starting Zapper Fetch...")
    positions = fetch_zapper_data()
    logging.info(f"Fetched {len(positions)} positions.")

    if not positions:
        logging.warning("No positions to insert.")
        return

    # 2. Connect & Insert
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        
        insert_query = """
        INSERT INTO lp_snapshots (address, protocol, network, position_label, balance_usd, assets, unclaimed)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        
        count = 0
        for p in positions:
            cur.execute(insert_query, (
                p["address"],
                p["protocol"],
                p["network"],
                p["position_label"],
                p["balance_usd"],
                json.dumps(p["assets"]),
                json.dumps(p["unclaimed"])
            ))
            count += 1
        
        conn.commit()
        cur.close()
        conn.close()
        logging.info(f"Successfully inserted {count} records into Postgres.")

    except Exception as e:
        logging.error(f"Database insertion failed: {e}")
        raise

with DAG(
    'zapper_balance_loader',
    default_args=default_args,
    description='Fetch Uniswap LP data via Zapper every 15 mins',
    schedule_interval='*/15 * * * *',
    start_date=days_ago(1),
    catchup=False,
    tags=['defi', 'uniswap'],
) as dag:

    run_etl = PythonOperator(
        task_id='fetch_and_load_zapper',
        python_callable=etl_process,
    )

    run_etl
