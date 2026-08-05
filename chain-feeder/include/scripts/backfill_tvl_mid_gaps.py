import sys
import os
import logging
import psycopg2

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
DAGS_DIR = os.path.join(ROOT_DIR, 'dags')
if DAGS_DIR not in sys.path:
    sys.path.insert(0, DAGS_DIR)
try:
    from common.utils.config import DATA_WAREHOUSE_DB
except ImportError:
    DATA_WAREHOUSE_DB = os.environ.get('DATA_WAREHOUSE_DB', 'postgresql://airflow:airflow@postgres:5432/airflow')


def backfill_tvl_mid_gaps():
    """
    Forward-fills zero/null TVL rows using the most recent non-zero TVL
    from any prior date for the same pool. Handles mid-range gaps that
    derive_swap_tvl_fallback.py misses.
    """
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    logging.info("Starting mid-range TVL gap backfill...")

    cur.execute("""
    WITH prev_tvl AS (
        SELECT h.ctid, h.pool_id, h.date,
            (SELECT h2.tvl_usd
             FROM liquidity_pool_history h2
             WHERE h2.pool_id = h.pool_id
               AND h2.date < h.date
               AND h2.tvl_usd IS NOT NULL AND ABS(h2.tvl_usd) > 1.0
             ORDER BY h2.date DESC
             LIMIT 1) as prev_tvl
        FROM liquidity_pool_history h
        WHERE h.tvl_usd IS NULL OR ABS(h.tvl_usd) <= 1.0
    )
    UPDATE liquidity_pool_history h
    SET tvl_usd = p.prev_tvl
    FROM prev_tvl p
    WHERE h.ctid = p.ctid
      AND p.prev_tvl IS NOT NULL
    """)
    updated = cur.rowcount
    conn.commit()
    logging.info(f"Mid-range gap backfill updated {updated} rows.")
    cur.close()
    conn.close()
    return updated


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    total = backfill_tvl_mid_gaps()
    print(f"Mid-range TVL backfill completed. Total rows updated: {total}")
