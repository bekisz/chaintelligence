import sys
import os
import logging
from datetime import datetime, timedelta, timezone

# Add routing module to sys.path for standalone testing
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ROUTING_DIR = os.path.join(ROOT_DIR, 'routing')
if ROUTING_DIR not in sys.path:
    sys.path.insert(0, ROUTING_DIR)

def get_db_connection():
    """Returns DB connection via PostgresHook (Airflow) or direct psycopg2 (CLI/standalone)."""
    try:
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        return pg_hook.get_conn()
    except Exception:
        import psycopg2
        from config import DATA_WAREHOUSE_DB
        return psycopg2.connect(DATA_WAREHOUSE_DB)

def run_global_volume_rollup(days_back: int = 14) -> int:
    """
    Aggregates daily transaction count and USD volume directly from the swaps table
    for ALL pools, protocols, and chains across the last N days.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    logging.info(f"Running global liquidity_pool_history volume rollup for last {days_back} days...")
    
    query = """
    INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd)
    SELECT
        s.pool_id AS pool_id,
        DATE(s.ts) AS date,
        COUNT(*) AS tx_count,
        SUM(s.amount_usd) AS volume_usd
    FROM swaps s
    WHERE s.amount_usd IS NOT NULL
      AND s.ts >= CURRENT_DATE - (INTERVAL '1 day' * %s)
    GROUP BY s.pool_id, DATE(s.ts)
    ON CONFLICT (pool_id, date) DO UPDATE
    SET tx_count = EXCLUDED.tx_count,
        volume_usd = EXCLUDED.volume_usd;
    """
    cur.execute(query, (days_back,))
    updated_rows = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Global volume rollup completed. Upserted {updated_rows} rows into liquidity_pool_history.")

    # Trigger TVL fallback backfill
    try:
        from include.scripts.derive_swap_tvl_fallback import derive_and_backfill_tvl_fallback
        fallback_rows = derive_and_backfill_tvl_fallback()
        logging.info(f"Automatic TVL fallback completed. Updated {fallback_rows} rows.")
    except Exception as e:
        logging.warning(f"Automatic TVL fallback skipped: {e}")

    return updated_rows

# Airflow DAG definition block
try:
    from airflow import DAG
    from airflow.sdk import task
    import pendulum

    default_args = {
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': False,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    }

    @task
    def execute_global_volume_rollup_task():
        return run_global_volume_rollup(days_back=14)

    with DAG(
    max_active_runs=1,
        'global_liquidity_pool_history_rollup',
        default_args=default_args,
        description='Unified daily volume and transaction count rollup for ALL liquidity pools',
        schedule='0 2 * * *',
        start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
        catchup=False,
        tags=['history', 'global', 'rollup'],
    ) as dag:
        rollup_task = execute_global_volume_rollup_task()

except ImportError:
    pass

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    rows = run_global_volume_rollup(days_back=30)
    print(f"Standalone rollup finished. Rows updated: {rows}")
