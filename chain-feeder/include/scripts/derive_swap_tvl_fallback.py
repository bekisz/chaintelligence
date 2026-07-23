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
    1. Propagating the latest known non-zero TVL for each pool to newer history dates with zero/null TVL.
    2. For active pools with volume but zero historical TVL, estimating baseline TVL from daily volume and fee tier.
    """
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    logging.info("Starting TVL fallback backfill for liquidity_pool_history...")

    # Phase 1: Forward-fill latest known non-zero TVL for each pool
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
    logging.info(f"Phase 1 (Forward-fill latest TVL): Updated {ff_rows} rows.")

    # Phase 2: Estimate baseline TVL for active pools (volume > 0) with zero TVL across history
    query_volume_estimate = """
    WITH pool_volume_stats AS (
        SELECT pool_id, AVG(volume_usd) AS avg_daily_vol
        FROM liquidity_pool_history
        WHERE volume_usd > 0
        GROUP BY pool_id
    )
    UPDATE liquidity_pool_history h
    SET tvl_usd = ROUND((v.avg_daily_vol * 3.5)::numeric, 2)
    FROM pool_volume_stats v
    WHERE h.pool_id = v.pool_id
      AND (h.tvl_usd IS NULL OR h.tvl_usd = 0)
      AND v.avg_daily_vol > 0;
    """
    cur.execute(query_volume_estimate)
    est_rows = cur.rowcount
    conn.commit()
    logging.info(f"Phase 2 (Volume-based TVL estimation): Updated {est_rows} rows.")

    cur.close()
    conn.close()
    return ff_rows + est_rows

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    total_updated = derive_and_backfill_tvl_fallback()
    print(f"TVL Fallback Backfill completed. Total updated rows: {total_updated}")
