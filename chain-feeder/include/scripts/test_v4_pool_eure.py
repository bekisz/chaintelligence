import sys
import os
import logging
from datetime import datetime, timedelta, timezone

os.environ["AIRFLOW_HOME"] = "/opt/airflow"
sys.path.append("/opt/airflow/dags")

from common.utils.uniswap_utils import UniswapV4Fetcher

logging.basicConfig(level=logging.INFO)

def main():
    import psycopg2
    from common.utils.config import DATA_WAREHOUSE_DB
    
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    
    symbol_map = {}
    cur.execute("""
        SELECT sym, addr, SUM(c) as total_c FROM (
            SELECT token0_symbol as sym, token0_address as addr, count(*) as c FROM uniswap_v4_swaps GROUP BY 1, 2
            UNION ALL 
            SELECT token1_symbol as sym, token1_address as addr, count(*) as c FROM uniswap_v4_swaps GROUP BY 1, 2
        ) t GROUP BY 1, 2 ORDER BY total_c ASC
    """)
    for row in cur.fetchall():
        sym, addr, count = row
        if sym and addr:
            symbol_map[sym.upper()] = addr.lower()
            if len(sym) > 8:
                symbol_map[sym[:8].upper()] = addr.lower()
                
    cur.execute("SELECT id, coin0_symbol, coin1_symbol, fee_tier FROM liquidity_pool WHERE id = 3213")
    pool = cur.fetchone()
    print("Pool:", pool)
    
    pool_id, c0, c1, fee = pool
    print("addr0 symbol_map:", symbol_map.get(c0.upper()))
    print("addr1 symbol_map:", symbol_map.get(c1.upper()))
    
    addr0 = symbol_map.get(c0.upper())
    addr1 = symbol_map.get(c1.upper())
    fee_bips = int(fee)
    
    fetcher = UniswapV4Fetcher(verbose=True)
    start_date = datetime.now(timezone.utc) - timedelta(days=2)
    data = fetcher.fetch_pool_daily_data(addr0, addr1, fee_bips, start_date)
    print("Fetched data:", data)

if __name__ == "__main__":
    main()
