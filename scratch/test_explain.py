import psycopg2
from datetime import datetime, timedelta
import sys

db_str = "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433"
conn = psycopg2.connect(db_str)
cur = conn.cursor()

end_dt = datetime.now()
start_dt = end_dt - timedelta(days=7)

q1 = """
EXPLAIN ANALYZE
SELECT id
FROM uniswap_v3_swaps
WHERE timestamp >= %s AND timestamp <= %s AND amount_usd >= 10.0 
AND network = 'Ethereum'
AND (token0_symbol = ANY(ARRAY['USDT', 'USDC']) OR token1_symbol = ANY(ARRAY['USDT', 'USDC']))
"""

cur.execute(q1, (start_dt, end_dt))
print("QUERY 1 (Original OR):")
for row in cur.fetchall():
    print(row[0])

q2 = """
EXPLAIN ANALYZE
SELECT id FROM uniswap_v3_swaps
WHERE timestamp >= %s AND timestamp <= %s AND amount_usd >= 10.0 AND network = 'Ethereum' AND token0_symbol = ANY(ARRAY['USDT', 'USDC'])
UNION
SELECT id FROM uniswap_v3_swaps
WHERE timestamp >= %s AND timestamp <= %s AND amount_usd >= 10.0 AND network = 'Ethereum' AND token1_symbol = ANY(ARRAY['USDT', 'USDC'])
"""

cur.execute(q2, (start_dt, end_dt, start_dt, end_dt))
print("\nQUERY 2 (UNION):")
for row in cur.fetchall():
    print(row[0])

cur.close()
conn.close()
