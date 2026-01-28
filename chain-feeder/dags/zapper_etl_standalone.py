import psycopg2
import json
import logging
from zapper_client import fetch_zapper_data

import os
from dotenv import load_dotenv

load_dotenv()

DB_CONN = os.getenv('DATA_WAREHOUSE_DB', "dbname=chaintelligence user=airflow password=airflow host=localhost port=5432")


def etl_process():
    logging.info("Starting Zapper Fetch...")
    positions = fetch_zapper_data()
    logging.info(f"Fetched {len(positions)} positions.")

    if not positions:
        logging.warning("No positions to insert.")
        return

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
    logging.basicConfig(level=logging.INFO)
    etl_process()
