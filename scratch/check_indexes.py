import psycopg2
import os

db_str = "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433"
conn = psycopg2.connect(db_str)
cur = conn.cursor()
cur.execute("""
    SELECT
        tablename,
        indexname,
        indexdef
    FROM
        pg_indexes
    WHERE
        schemaname = 'public' AND tablename IN ('uniswap_v3_swaps', 'uniswap_v4_swaps', 'liquidity_pool_history', 'liquidity_pool');
""")
for row in cur.fetchall():
    print(row)
cur.close()
conn.close()
