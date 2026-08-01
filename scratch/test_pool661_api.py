import os
import requests
import json
import psycopg2
import re
from dotenv import load_dotenv

load_dotenv('.env.config')
load_dotenv('.env.secrets')

# Query DB directly for pool 661 stats
db_url = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=localhost port=5433')
db_url = re.sub(r'host=\S+', 'host=localhost', db_url)
db_url = re.sub(r'port=\S+', 'port=5433', db_url)

conn = psycopg2.connect(db_url)
cur = conn.cursor()

print("=== Pool 661 DB Fields ===")
cur.execute("""
    SELECT id, pool_name, pool_id, fee_bps, reverted, coin0_id, coin1_id, chain_id, protocol_id
    FROM liquidity_pool WHERE id = 661
""")
print("liquidity_pool:", cur.fetchone())

print("\n=== Latest History Entry for Pool 661 ===")
cur.execute("""
    SELECT date, tvl_usd, volume_usd, tx_count
    FROM liquidity_pool_history
    WHERE pool_id = 661
    ORDER BY date DESC LIMIT 5;
""")
for r in cur.fetchall():
    print(r)

print("\n=== Check total swaps volume/count for Pool 661 ===")
cur.execute("""
    SELECT count(*), min(block_timestamp), max(block_timestamp)
    FROM swaps WHERE pool_id = 661
""")
print("Swaps count & range:", cur.fetchone())

cur.close()
conn.close()

# Also query API server if running on localhost:8000
try:
    resp = requests.get('http://localhost:8000/api/pool/661', timeout=3)
    print("\n=== API /api/pool/661 ===")
    print("Status:", resp.status_code)
    print("Response:", resp.json())
except Exception as e:
    print("\nAPI query to /api/pool/661 failed/not running:", e)
