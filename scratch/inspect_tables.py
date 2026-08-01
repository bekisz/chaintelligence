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

print("--- Public Tables ---")
cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public';
""")
tables = [r[0] for r in cur.fetchall()]
print(sorted(tables))

print("\n--- Check coin_contract for EURI (coin_id=51) and USDC (coin_id=9) ---")
cur.execute("SELECT * FROM coin_contract WHERE coin_id IN (51, 9);")
for r in cur.fetchall():
    print(r)

print("\n--- Check table schemas related to pool ---")
for t in sorted(tables):
    if 'pool' in t or 'swap' in t or 'stat' in t or 'lp' in t or 'snapshot' in t:
        print(f"Table: {t}")

cur.close()
conn.close()
