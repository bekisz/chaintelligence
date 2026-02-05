import psycopg2
from config import DATA_WAREHOUSE_DB

try:
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    cur.execute("SELECT fee_tier FROM uniswap_v3_swaps LIMIT 10")
    rows = cur.fetchall()
    print("Fee tiers in DB:", [row[0] for row in rows])
    cur.close()
    conn.close()
except Exception as e:
    print(e)
