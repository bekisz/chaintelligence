import os
import psycopg2
import re
from dotenv import load_dotenv

load_dotenv('.env.config')
load_dotenv('.env.secrets')

db_url = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=localhost port=5433')
db_url = re.sub(r'host=\S+', 'host=localhost', db_url)
db_url = re.sub(r'port=\S+', 'port=5433', db_url)

conn = psycopg2.connect(db_url)
cur = conn.cursor()

print("--- Liquidity Pool 661 ---")
cur.execute("""
    SELECT lp.*, c0.symbol as coin0_symbol, c0.name as coin0_name, c1.symbol as coin1_symbol, c1.name as coin1_name,
           pr.name as protocol_name, ch.name as chain_name
    FROM liquidity_pool lp
    LEFT JOIN coin c0 ON lp.coin0_id = c0.coin_id
    LEFT JOIN coin c1 ON lp.coin1_id = c1.coin_id
    LEFT JOIN protocol pr ON lp.protocol_id = pr.id
    LEFT JOIN chain ch ON lp.chain_id = ch.id
    WHERE lp.id = 661;
""")
row = cur.fetchone()
colnames = [desc[0] for desc in cur.description]
for col, val in zip(colnames, row):
    print(f"  {col}: {val}")

print("\n--- Daily Snapshots (last 10) ---")
cur.execute("""
    SELECT * FROM liquidity_pool_daily_snapshot 
    WHERE pool_id = 661 
    ORDER BY snapshot_date DESC LIMIT 10;
""")
snapshots = cur.fetchall()
if snapshots:
    colnames = [desc[0] for desc in cur.description]
    for s in snapshots:
        print(dict(zip(colnames, s)))

print("\n--- Recent Swaps for Pool 661 (last 5) ---")
cur.execute("""
    SELECT * FROM swaps 
    WHERE pool_id = 661 
    ORDER BY block_timestamp DESC LIMIT 5;
""")
swaps = cur.fetchall()
if swaps:
    colnames = [desc[0] for desc in cur.description]
    for sw in swaps:
        print(dict(zip(colnames, sw)))

cur.close()
conn.close()
