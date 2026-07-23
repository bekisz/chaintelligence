import sys
import os
import logging
from datetime import datetime, timedelta, timezone

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
ROUTING_DIR = os.path.join(ROOT_DIR, 'routing')
if ROUTING_DIR not in sys.path:
    sys.path.insert(0, ROUTING_DIR)

from config import DATA_WAREHOUSE_DB
import psycopg2

def derive_and_backfill_tvl_fallback():
    """
    Backfills missing TVL in liquidity_pool_history by:
    Propagating the latest known non-zero real TVL snapshot for each pool
    to adjacent history dates where TVL is zero or null.
    """
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    logging.info("Starting TVL fallback backfill for liquidity_pool_history...")

    # Forward-fill latest known non-zero TVL for each pool
    query_forward_fill = """
    WITH latest_known_tvl AS (
        SELECT DISTINCT ON (pool_id) pool_id, ABS(tvl_usd) AS tvl, date
        FROM liquidity_pool_history
        WHERE tvl_usd IS NOT NULL AND tvl_usd > 0
        ORDER BY pool_id, date DESC
    )
    UPDATE liquidity_pool_history h
    SET tvl_usd = l.tvl
    FROM latest_known_tvl l
    WHERE h.pool_id = l.pool_id
      AND (h.tvl_usd IS NULL OR h.tvl_usd = 0)
      AND h.date >= l.date;
    """
    cur.execute(query_forward_fill)
    ff_rows = cur.rowcount
    conn.commit()
    logging.info(f"Forward-fill latest TVL: Updated {ff_rows} rows.")

    cur.close()
    conn.close()
    return ff_rows

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    total_updated = derive_and_backfill_tvl_fallback()
    print(f"TVL Fallback Backfill completed. Total updated rows: {total_updated}")
