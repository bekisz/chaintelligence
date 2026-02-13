#!/usr/bin/env python3
"""
Clean position_event table by removing records with zero amounts
"""
import os
import psycopg2
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

DB_CONN = os.getenv("DATA_WAREHOUSE_DB")

def clean_events():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # Count records before
    cur.execute("SELECT COUNT(*) FROM liquidity_pool_position_event")
    before = cur.fetchone()[0]
    logger.info(f"Total records before: {before}")
    
    # Delete ALL events
    cur.execute("DELETE FROM liquidity_pool_position_event")
    deleted = cur.rowcount
    logger.info(f"Deleted {deleted} total events")
    
    conn.commit()
    
    # Count after
    cur.execute("SELECT COUNT(*) FROM liquidity_pool_position_event")
    after = cur.fetchone()[0]
    logger.info(f"Total records after: {after}")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    clean_events()
