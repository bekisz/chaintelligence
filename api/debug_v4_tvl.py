import psycopg2
import os

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")
if os.path.exists('/.dockerenv'):
    DB_CONN = DB_CONN.replace('host=localhost', 'host=postgres').replace('port=5433', 'port=5432')

try:
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # Let's check if any Uniswap V4 pools have non-zero TVL history
    cur.execute("""
        SELECT count(*), count(CASE WHEN h.tvl_usd <> 0 THEN 1 END)
        FROM liquidity_pool_history h
        JOIN liquidity_pool lp ON h.pool_id = lp.id
        WHERE lp.protocol = 'Uniswap V4';
    """)
    print("Uniswap V4 history TVL counts:")
    print(cur.fetchone())
    
    # Let's check if any PancakeSwap V4 pools have non-zero TVL history
    cur.execute("""
        SELECT count(*), count(CASE WHEN h.tvl_usd <> 0 THEN 1 END)
        FROM liquidity_pool_history h
        JOIN liquidity_pool lp ON h.pool_id = lp.id
        WHERE lp.protocol = 'PancakeSwap V4';
    """)
    print("PancakeSwap V4 history TVL counts:")
    print(cur.fetchone())
    
    cur.close()
    conn.close()
except Exception as e:
    print("Error:", e)
