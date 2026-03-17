import sys
import os
from datetime import datetime, timedelta
import logging

# Add paths to sys.path
ROOT_DIR = '/app'
sys.path.insert(0, os.path.join(ROOT_DIR, 'chain-feeder', 'dags'))

from common.utils.uniswap_utils import UniswapV3Fetcher
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATA_WAREHOUSE_DB = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=postgres port=5432')

logging.basicConfig(level=logging.INFO)

def force_sync_tvl():
    fetcher = UniswapV3Fetcher(verbose=True)
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    
    print("Building symbol address map...")
    symbol_map = {}
    
    # Priority 2: Swaps
    cur.execute("""
        SELECT DISTINCT token0_symbol, token0_address FROM uniswap_v3_swaps
        UNION 
        SELECT DISTINCT token1_symbol, token1_address FROM uniswap_v3_swaps
    """)
    for row in cur.fetchall():
        sym, addr = row
        if sym and addr:
            symbol_map[sym.upper()] = addr.lower()
            if len(sym) > 8:
                symbol_map[sym[:8].upper()] = addr.lower()

    # Priority 1: coin table
    cur.execute("SELECT symbol, ethereum_address FROM coin WHERE ethereum_address IS NOT NULL")
    for row in cur.fetchall():
        sym, addr = row
        if sym and addr:
            symbol_map[sym.upper()] = addr.lower()
    
    print("Fetching pools with 0 TVL...")
    cur.execute("""
        SELECT DISTINCT p.id, p.coin0_symbol, p.coin1_symbol, p.fee_tier 
        FROM liquidity_pool p
        LEFT JOIN liquidity_pool_history h ON p.id = h.pool_id
        WHERE h.tvl_usd = 0 OR h.tvl_usd IS NULL OR h.pool_id IS NULL
    """)
    pools = cur.fetchall()
    print(f"Found {len(pools)} pools with missing TVL data.")
    
    start_date = datetime.now() - timedelta(days=90)
    
    count = 0
    for pool in pools:
        pool_id, c0, c1, fee = pool
        if not fee: continue
        
        addr0 = symbol_map.get(c0.upper())
        addr1 = symbol_map.get(c1.upper())
        
        if not addr0 or not addr1:
            continue
            
        try:
            data = fetcher.fetch_pool_daily_data(addr0, addr1, int(fee), start_date)
            if data:
                print(f"Updating {len(data)} records for {c0}-{c1} ({fee})")
                for d in data:
                    cur.execute("""
                        INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd, tvl_usd)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (pool_id, date) DO UPDATE 
                        SET tvl_usd = EXCLUDED.tvl_usd,
                            volume_usd = EXCLUDED.volume_usd,
                            tx_count = EXCLUDED.tx_count;
                    """, (pool_id, d['date'], d['tx_count'], d['volume_usd'], d['tvl_usd']))
                conn.commit()
                count += 1
        except Exception as e:
            print(f"Error for pool {c0}-{c1}: {e}")
            
    cur.close()
    conn.close()
    print(f"Synced {count} pools.")

if __name__ == "__main__":
    force_sync_tvl()
