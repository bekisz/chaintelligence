
import psycopg2
import os
from datetime import datetime, timedelta

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def test_exact_query():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    t0_sym = 'EURC'
    t1_sym = 'EURCV'
    fee_tier = '0.01%'
    protocol = 'Uniswap V3'
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    
    query = """
        SELECT token0_symbol, token1_symbol, SUM(amount_usd), SUM(ABS(amount0)), SUM(ABS(amount1)) FROM (
            SELECT amount_usd, amount0, amount1, timestamp, token0_symbol, token1_symbol, fee_tier, 'Uniswap V3' as protocol FROM uniswap_v3_swaps
            UNION ALL
            SELECT amount_usd, amount0, amount1, timestamp, token0_symbol, token1_symbol, fee_tier, 'Uniswap V4' as protocol FROM uniswap_v4_swaps
        ) as all_swaps
        WHERE timestamp >= %s AND timestamp <= %s
        AND protocol = %s
        AND (
            (UPPER(token0_symbol) = %s AND UPPER(token1_symbol) = %s)
            OR 
            (UPPER(token0_symbol) = %s AND UPPER(token1_symbol) = %s)
        )
        AND fee_tier = %s
        GROUP BY token0_symbol, token1_symbol
    """
    params = (start_date, end_date, protocol, t0_sym, t1_sym, t1_sym, t0_sym, fee_tier)
    print(f"Executing with params: {params}")
    cur.execute(query, params)
    rows = cur.fetchall()
    print(f"Results: {rows}")

if __name__ == "__main__":
    test_exact_query()
