
import psycopg2
import os

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def fix_eurc_final():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    official_addr = '0x1abaea1f7c830bd89acc67ec4af516284b1bc33c'
    bad_symbol = 'EURC'
    good_symbol = 'EUROC'
    
    try:
        print("Starting comprehensive EURC fix...")
        
        # 1. Clear references to the 'bad' EURC
        # We delete pools and swaps associated with the 'bad' EURC to start fresh
        # (They only have $1.9k volume anyway)
        
        cur.execute("DELETE FROM liquidity_pool_history WHERE pool_id IN (SELECT id FROM liquidity_pool WHERE coin0_symbol = %s OR coin1_symbol = %s)", (bad_symbol, bad_symbol))
        print(f"Deleted history for bad EURC pools. Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM liquidity_pool WHERE coin0_symbol = %s OR coin1_symbol = %s", (bad_symbol, bad_symbol))
        print(f"Deleted bad EURC pools. Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM uniswap_v4_swaps WHERE token0_symbol = %s OR token1_symbol = %s", (bad_symbol, bad_symbol))
        print(f"Deleted bad EURC swaps (V4). Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM uniswap_v3_swaps WHERE token0_symbol = %s OR token1_symbol = %s", (bad_symbol, bad_symbol))
        print(f"Deleted bad EURC swaps (V3). Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM coin_price_history WHERE symbol = %s", (bad_symbol,))
        print(f"Deleted bad EURC price history. Rows: {cur.rowcount}")

        # 2. Now the symbol 'EURC' should be freeable from the coin table
        cur.execute("DELETE FROM coin WHERE symbol = %s", (bad_symbol,))
        print(f"Deleted bad EURC entry from coin table. Rows: {cur.rowcount}")
        
        # 3. Rename EUROC to EURC in coin table
        cur.execute("UPDATE coin SET symbol = 'EURC', decimals = 6 WHERE symbol = 'EUROC'")
        print(f"Renamed EUROC to EURC in coin table. Rows: {cur.rowcount}")
        
        # 4. Update all references from EUROC to EURC
        tables_to_update = [
            ("liquidity_pool", "coin0_symbol"),
            ("liquidity_pool", "coin1_symbol"),
            ("uniswap_v3_swaps", "token0_symbol"),
            ("uniswap_v3_swaps", "token1_symbol"),
            ("uniswap_v4_swaps", "token0_symbol"),
            ("uniswap_v4_swaps", "token1_symbol"),
            ("coin_price_history", "symbol")
        ]
        
        for table, col in tables_to_update:
            cur.execute(f"UPDATE {table} SET {col} = 'EURC' WHERE {col} = 'EUROC'")
            print(f"Updated {table}.{col} (EUROC -> EURC). Rows: {cur.rowcount}")
            
        # 5. Update pool names
        cur.execute("UPDATE liquidity_pool SET pool_name = REPLACE(pool_name, 'EUROC', 'EURC') WHERE pool_name LIKE '%%EUROC%%'")
        print(f"Updated pool names. Rows: {cur.rowcount}")

        conn.commit()
        print("\n✅ Migration Finished. The 'real' EURC is now active with full history!")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error during migration: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    fix_eurc_final()
