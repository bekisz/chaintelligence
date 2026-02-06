import psycopg2
from config import DATA_WAREHOUSE_DB

try:
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    
    # Check what families exist
    cur.execute("SELECT DISTINCT family FROM coin")
    families = cur.fetchall()
    print("Families:", [f[0] for f in families])
    
    # Check members of 'USD'
    cur.execute("SELECT symbol FROM coin WHERE family = 'USD'")
    usd_coins = cur.fetchall()
    print("USD Family:", [c[0] for c in usd_coins])
    
    cur.close()
    conn.close()
except Exception as e:
    print(e)
