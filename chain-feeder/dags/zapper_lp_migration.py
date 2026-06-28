from airflow import DAG
from airflow.sdk import task, Param, Asset
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
import logging

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'retry_delay': pendulum.duration(minutes=1),
}

@task
def delete_null_pool_positions():
    """Delete any liquidity_pool_position rows that have a NULL pool_id.
    These rows are incomplete and cannot be linked to a pool.
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM liquidity_pool_position WHERE pool_id IS NULL;")
        count = cur.fetchone()[0]
        logging.info(f"Found {count} liquidity_pool_position rows with NULL pool_id. Deleting them.")
        cur.execute("DELETE FROM liquidity_pool_position WHERE pool_id IS NULL;")
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error(f"Error cleaning NULL pool_id rows: {e}")
        raise
    finally:
        cur.close()
        conn.close()

@task
def enforce_not_null_constraint():
    """Alter the column to be NOT NULL. This will fail if any NULLs remain.
    It should be safe after the cleanup task above.
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE liquidity_pool_position ALTER COLUMN pool_id SET NOT NULL;")
        conn.commit()
        logging.info("Successfully set pool_id NOT NULL on liquidity_pool_position.")
    except Exception as e:
        conn.rollback()
        logging.error(f"Error altering pool_id constraint: {e}")
        raise
    finally:
        cur.close()
        conn.close()

with DAG(
    dag_id='zapper_lp_migration',
    default_args=default_args,
    description='Migration to enforce pool_id NOT NULL for liquidity_pool_position',
    schedule=None,
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['migration', 'zapper', 'liquidity'],
) as dag:
    clean = delete_null_pool_positions()
    alter = enforce_not_null_constraint()
    clean >> alter
