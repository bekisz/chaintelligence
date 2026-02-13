import os
import psycopg2
import logging

logging.basicConfig(level=logging.INFO)

# Config
DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "postgres://airflow:airflow@postgres/chaintelligence")

def reset_scan():
    try:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        
        print("Resetting last_claim_scan_block for ALL positions...")
        cur.execute("UPDATE liquidity_pool_position SET last_claim_scan_block = 0")
        conn.commit()
        print(f"Updated {cur.rowcount} rows.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    reset_scan()
