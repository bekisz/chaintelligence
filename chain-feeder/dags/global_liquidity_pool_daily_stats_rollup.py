import sys
import os
import logging
from datetime import datetime, timedelta, timezone

# Add dags module to sys.path for standalone testing
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DAGS_DIR = os.path.join(ROOT_DIR, 'dags')
if DAGS_DIR not in sys.path:
    sys.path.insert(0, DAGS_DIR)

def zero_fill_dormant_pools(days_back: int = 14):
    """
    Creates a zero-row (tx_count=0, volume_usd=0, tvl_usd=NULL) in
    liquidity_pool_daily_stats for every pool that does NOT already have a row
    on each date in the lookback window. This ensures dormant pools (0 swaps)
    still appear in coverage queries and the TVL fallback can propagate TVL
    to them.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    logging.info(f"Zero-filling dormant pool history rows for last {days_back} days...")

    cur.execute("""
        INSERT INTO liquidity_pool_daily_stats (pool_id, day, tx_count, volume_usd)
        SELECT lp.id, (CURRENT_DATE - s.day_offset)::date, 0, 0.0
        FROM liquidity_pool lp
        CROSS JOIN generate_series(1, %s) s(day_offset)
        WHERE NOT EXISTS (
            SELECT 1 FROM liquidity_pool_daily_stats h
            WHERE h.pool_id = lp.id AND h.day = (CURRENT_DATE - s.day_offset)::date
        )
        ON CONFLICT (pool_id, day) DO NOTHING
    """, (days_back,))
    inserted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Zero-filled {inserted} dormant pool-day rows.")
    return inserted


def get_db_connection():
    """Returns DB connection via PostgresHook (Airflow) or direct psycopg2 (CLI/standalone)."""
    try:
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        return pg_hook.get_conn()
    except Exception:
        import psycopg2
        from common.utils.config import DATA_WAREHOUSE_DB
        return psycopg2.connect(DATA_WAREHOUSE_DB)

def run_global_volume_rollup(days_back: int = 14, table_name: str = None) -> int:
    """
    Aggregates daily transaction count and USD volume directly from the short-lived
    raw swap store for ALL pools, protocols, and chains across the last N days.
    """
    if not table_name:
        import os
        table_name = os.getenv('SWAP_RAW_TABLE', 'swaps_staging').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    
    logging.info(f"Running global liquidity_pool_daily_stats volume rollup for last {days_back} days (from {table_name})...")
    
    query = f"""
    INSERT INTO liquidity_pool_daily_stats (pool_id, day, tx_count, volume_usd)
    SELECT
        s.pool_id AS pool_id,
        DATE(s.ts) AS day,
        COUNT(*) AS tx_count,
        SUM(ABS(s.amount_usd)) AS volume_usd
    FROM {table_name} s
    WHERE s.amount_usd IS NOT NULL
      AND s.ts >= CURRENT_DATE - (INTERVAL '1 day' * {days_back})
    GROUP BY s.pool_id, DATE(s.ts)
    ON CONFLICT (pool_id, day) DO UPDATE
    SET tx_count = EXCLUDED.tx_count,
        volume_usd = EXCLUDED.volume_usd;
    """
    cur.execute(query)
    updated_rows = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    # Rebuild configured pool swap-size distribution buckets for the recent window.
    try:
        from include.route_classifier import recompute_pool_distribution_buckets
        buckets_conn = get_db_connection()
        try:
            with buckets_conn.cursor() as cur:
                days = [(datetime.now(timezone.utc) - timedelta(days=i)).strftime('%Y-%m-%d')
                        for i in range(days_back + 1)]
                recompute_pool_distribution_buckets(cur, days, table_name=table_name)
            buckets_conn.commit()
        finally:
            buckets_conn.close()
    except Exception as e:
        logging.warning(f"Pool distribution bucket rebuild skipped: {e}")
    logging.info(f"Global volume rollup completed. Upserted {updated_rows} rows into liquidity_pool_daily_stats.")

    # Zero-fill dormant pools so every pool has a history row (even with 0 tx/vol)
    try:
        zero_fill_dormant_pools(days_back)
    except Exception as e:
        logging.warning(f"Zero-fill skipped: {e}")

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
    'global_liquidity_pool_daily_stats_rollup',
    max_active_runs=1,
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
    zf = zero_fill_dormant_pools(days_back=30)
    print(f"Standalone rollup finished. Rows updated: {rows}. Zero-filled: {zf}.")
