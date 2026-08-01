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

print("=== Swaps for Pool 661 (Latest 5) ===")
cur.execute("SELECT * FROM swaps WHERE pool_id = 661 ORDER BY ts DESC LIMIT 5;")
rows = cur.fetchall()
if rows:
    colnames = [desc[0] for desc in cur.description]
    for r in rows:
        print(dict(zip(colnames, r)))

print("\n=== Swaps Count for Pool 661 ===")
cur.execute("SELECT count(*) FROM swaps WHERE pool_id = 661;")
print("Count:", cur.fetchone()[0])

cur.close()
conn.close()
