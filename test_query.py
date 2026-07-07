import psycopg2, time
from datetime import datetime, timedelta

DATA_WAREHOUSE_DB = "postgresql://airflow:airflow@localhost:5433/chaintelligence"
conn = psycopg2.connect(DATA_WAREHOUSE_DB)
cur = conn.cursor()

start_dt = datetime.fromisoformat("2026-06-24T00:00:00+00:00")
end_dt = datetime.fromisoformat("2026-07-01T23:59:59+00:00")

start_tokens = ["USDC", "USDT", "DAI", "FRAX"]
end_tokens = ["ETH", "WETH", "STETH", "RETH"]

query = """
WITH relevant_txs AS (
    SELECT tx_hash 
    FROM uniswap_v3_swaps
    WHERE timestamp >= %s AND timestamp <= %s
    GROUP BY tx_hash
    HAVING 
        bool_or(token0_symbol = ANY(%s) OR token1_symbol = ANY(%s))
        AND 
        bool_or(token0_symbol = ANY(%s) OR token1_symbol = ANY(%s))
)
SELECT COUNT(*) FROM uniswap_v3_swaps
WHERE timestamp >= %s AND timestamp <= %s
AND tx_hash IN (SELECT tx_hash FROM relevant_txs)
"""

t0 = time.time()
cur.execute(query, (
    start_dt, end_dt,
    start_tokens, start_tokens,
    end_tokens, end_tokens,
    start_dt, end_dt
))
res = cur.fetchone()[0]
print(f"Count: {res}, Time: {time.time()-t0:.2f}s")
