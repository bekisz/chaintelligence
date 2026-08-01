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

print("=== POOL HISTORY COLUMNS ===")
cur.execute("SELECT * FROM liquidity_pool_history LIMIT 1;")
row = cur.fetchone()
if row:
    colnames = [desc[0] for desc in cur.description]
    print(colnames)

print("\n=== POOL 661 HISTORY ===")
cur.execute("SELECT * FROM liquidity_pool_history WHERE pool_id = 661;")
rows = cur.fetchall()
if rows:
    colnames = [desc[0] for desc in cur.description]
    for r in rows:
        print(dict(zip(colnames, r)))
else:
    print("No rows found for pool_id = 661 in liquidity_pool_history.")

cur.close()
conn.close()
