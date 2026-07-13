import psycopg2
import os

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

try:
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # Let's search for PancakeSwap V3 pools with ETH on BNB Chain
    cur.execute("""
        SELECT lp.id, lp.pool_name, lp.fee_tier, lp.pool_id, lp.coin0_id, lp.coin1_id,
               c0.symbol, c1.symbol
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE lp.protocol = 'PancakeSwap V3' AND lp.network = 'BNB'
          AND (UPPER(c0.symbol) = 'ETH' OR UPPER(c1.symbol) = 'ETH');
    """)
    pools = cur.fetchall()
    print(f"Found {len(pools)} PancakeSwap V3 pools containing ETH on BNB:")
    for p in pools:
        print(p)
        pid = p[0]
        # Let's see if history exists
        cur.execute("""
            SELECT count(*), sum(volume_usd), sum(tvl_usd)
            FROM liquidity_pool_history
            WHERE pool_id = %s;
        """, (pid,))
        h_row = cur.fetchone()
        print(f"  History stats: count={h_row[0]}, sum_vol={h_row[1]}, sum_tvl={h_row[2]}")
        
    cur.close()
    conn.close()
except Exception as e:
    print("Error:", e)
