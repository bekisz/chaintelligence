import sys
import os
import logging
from datetime import datetime, timedelta, timezone

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
import psycopg2

def derive_and_backfill_tvl_fallback():
    """
    Backfills missing or near-zero TVL in liquidity_pool_history by:
    1. Forward-filling the latest known non-zero real TVL snapshot (> $1.0)
       for each pool to subsequent history dates where TVL is zero, <= 1.0, or null.
    2. Backward-filling the earliest known real TVL snapshot (> $1.0)
       to preceding history dates if no earlier non-zero TVL exists.
    """
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    logging.info("Starting TVL fallback backfill for liquidity_pool_history...")

    # 1. Forward-fill latest known non-zero TVL (> $1.0) for each pool
    query_forward_fill = """
    WITH latest_known_tvl AS (
        SELECT DISTINCT ON (pool_id) pool_id, ABS(tvl_usd) AS tvl, date
        FROM liquidity_pool_history
        WHERE tvl_usd IS NOT NULL AND ABS(tvl_usd) > 1.0
        ORDER BY pool_id, date DESC
    )
    UPDATE liquidity_pool_history h
    SET tvl_usd = l.tvl
    FROM latest_known_tvl l
    WHERE h.pool_id = l.pool_id
      AND (h.tvl_usd IS NULL OR ABS(h.tvl_usd) <= 1.0)
      AND h.date >= l.date;
    """
    cur.execute(query_forward_fill)
    ff_rows = cur.rowcount

    # 2. Backward-fill earliest known non-zero TVL (> $1.0) for remaining zero/null rows
    query_backward_fill = """
    WITH earliest_known_tvl AS (
        SELECT DISTINCT ON (pool_id) pool_id, ABS(tvl_usd) AS tvl, date
        FROM liquidity_pool_history
        WHERE tvl_usd IS NOT NULL AND ABS(tvl_usd) > 1.0
        ORDER BY pool_id, date ASC
    )
    UPDATE liquidity_pool_history h
    SET tvl_usd = e.tvl
    FROM earliest_known_tvl e
    WHERE h.pool_id = e.pool_id
      AND (h.tvl_usd IS NULL OR ABS(h.tvl_usd) <= 1.0)
      AND h.date < e.date;
    """
    cur.execute(query_backward_fill)
    bf_rows = cur.rowcount

    conn.commit()
    total_updated = ff_rows + bf_rows
    logging.info(f"TVL Fallback Backfill: Forward-filled {ff_rows} rows, Backward-filled {bf_rows} rows.")

    cur.close()
    conn.close()
    return total_updated

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    total_updated = derive_and_backfill_tvl_fallback()
    print(f"TVL Fallback Backfill completed. Total updated rows: {total_updated}")
