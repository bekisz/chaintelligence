#!/usr/bin/env python3
"""
Standalone Zapper ETL script - runs outside of Airflow
Can be executed via cron or manually
"""
import sys
import os
import logging
import psycopg2
import json

# Add dags directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'dags'))

from zapper_client import fetch_zapper_data

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from dotenv import load_dotenv

load_dotenv()

DB_CONN = os.getenv('DATA_WAREHOUSE_DB', "dbname=chaintelligence user=airflow password=airflow host=localhost port=5432")


def etl_process():
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
        INSERT INTO lp_snapshots (address, protocol, network, position_label, balance_usd, assets, unclaimed, images)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
                json.dumps(p["unclaimed"]),
                json.dumps(p.get("images", []))
            ))
            count += 1
        
        conn.commit()
        cur.close()
        conn.close()
        logging.info(f"Successfully inserted {count} records into Postgres.")

    except Exception as e:
        logging.error(f"Database insertion failed: {e}")
        raise

if __name__ == "__main__":
    try:
        logging.info("Starting Zapper ETL process...")
        etl_process()
        logging.info("Zapper ETL process completed successfully")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Zapper ETL process failed: {e}", exc_info=True)
        sys.exit(1)
