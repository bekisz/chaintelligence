
import psycopg2
import os

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def fix_eurc_mega_final():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    try:
        print("Starting mega final EURC fix...")
        
        # Disable FK checks
        cur.execute("SET session_replication_role = 'replica';")
        
        # 1. DELETE all traces of the current 'EURC' (the bad one)
        # This includes pools, swaps, and the coin entry.
        bad_sym = 'EURC'
        
        cur.execute("DELETE FROM liquidity_pool_history WHERE pool_id IN (SELECT id FROM liquidity_pool WHERE coin0_symbol = %s OR coin1_symbol = %s)", (bad_sym, bad_sym))
        print(f"Deleted history for bad EURC pools. Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM liquidity_pool WHERE coin0_symbol = %s OR coin1_symbol = %s", (bad_sym, bad_sym))
        print(f"Deleted bad EURC pools. Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM uniswap_v4_swaps WHERE token0_symbol = %s OR token1_symbol = %s", (bad_sym, bad_sym))
        print(f"Deleted bad EURC swaps (V4). Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM uniswap_v3_swaps WHERE token0_symbol = %s OR token1_symbol = %s", (bad_sym, bad_sym))
        print(f"Deleted bad EURC swaps (V3). Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM coin WHERE symbol = %s", (bad_sym,))
        print(f"Deleted bad EURC coin. Rows: {cur.rowcount}")

        # 2. Rename EUROC to EURC
        good_sym = 'EUROC'
        cur.execute("UPDATE coin SET symbol = 'EURC', decimals = 6 WHERE symbol = %s", (good_sym,))
        print(f"Renamed EUROC coin to EURC. Rows: {cur.rowcount}")
        
        # 3. Update references
        cur.execute("UPDATE liquidity_pool SET coin0_symbol = 'EURC' WHERE coin0_symbol = %s", (good_sym,))
        print(f"Updated liquidity_pool.coin0_symbol. Rows: {cur.rowcount}")
        cur.execute("UPDATE liquidity_pool SET coin1_symbol = 'EURC' WHERE coin1_symbol = %s", (good_sym,))
        print(f"Updated liquidity_pool.coin1_symbol. Rows: {cur.rowcount}")
        
        cur.execute("UPDATE uniswap_v3_swaps SET token0_symbol = 'EURC' WHERE token0_symbol = %s", (good_sym,))
        print(f"Updated uniswap_v3_swaps.token0_symbol. Rows: {cur.rowcount}")
        cur.execute("UPDATE uniswap_v3_swaps SET token1_symbol = 'EURC' WHERE token1_symbol = %s", (good_sym,))
        print(f"Updated uniswap_v3_swaps.token1_symbol. Rows: {cur.rowcount}")
        
        cur.execute("UPDATE uniswap_v4_swaps SET token0_symbol = 'EURC' WHERE token0_symbol = %s", (good_sym,))
        print(f"Updated uniswap_v4_swaps.token0_symbol. Rows: {cur.rowcount}")
        cur.execute("UPDATE uniswap_v4_swaps SET token1_symbol = 'EURC' WHERE token1_symbol = %s", (good_sym,))
        print(f"Updated uniswap_v4_swaps.token1_symbol. Rows: {cur.rowcount}")
        
        cur.execute("UPDATE coin_price_history SET symbol = 'EURC' WHERE symbol = %s", (good_sym,))
        print(f"Updated coin_price_history. Rows: {cur.rowcount}")
        
        cur.execute("UPDATE liquidity_pool SET pool_name = REPLACE(pool_name, 'EUROC', 'EURC') WHERE pool_name LIKE '%%EUROC%%'")
        print(f"Updated pool names. Rows: {cur.rowcount}")

        # Re-enable FK checks
        cur.execute("SET session_replication_role = 'origin';")
        conn.commit()
        print("\n✅ Success! EURC is now the official one with all history merged.")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    fix_eurc_mega_final()
