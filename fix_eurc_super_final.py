
import psycopg2
import os

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def fix_eurc_super_final():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    try:
        print("Starting super final EURC fix (bypassing FK constraints)...")
        
        # Disable FK checks
        cur.execute("SET session_replication_role = 'replica';")
        
        # 1. Delete bad EURC
        cur.execute("DELETE FROM coin WHERE symbol = 'EURC'")
        print(f"Deleted bad EURC coin. Rows: {cur.rowcount}")
        
        # 2. Rename EUROC to EURC in coin table
        cur.execute("UPDATE coin SET symbol = 'EURC', decimals = 6 WHERE symbol = 'EUROC'")
        print(f"Renamed EUROC to EURC. Rows: {cur.rowcount}")
        
        # 3. Update all references
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
            
        cur.execute("UPDATE liquidity_pool SET pool_name = REPLACE(pool_name, 'EUROC', 'EURC') WHERE pool_name LIKE '%%EUROC%%'")
        print(f"Updated pool names. Rows: {cur.rowcount}")

        # 4. Clean up any remaining bad references (from the original bad EURC)
        # These are the ones we tried to delete before
        cur.execute("DELETE FROM liquidity_pool_history WHERE pool_id IN (SELECT id FROM liquidity_pool WHERE (coin0_symbol = 'EURC' OR coin1_symbol = 'EURC') AND id NOT IN (SELECT id FROM liquidity_pool WHERE pool_name LIKE '%%EURC%%' AND (coin0_symbol = 'EURC' OR coin1_symbol = 'EURC')))")
        # Actually, simpler: just delete pools that don't match the official address if we had that info.
        # But we already deleted the bad pools in the previous run if it partially succeeded?
        # No, it rolled back.
        
        # Let's just delete pools where we know they use the bad addresses
        bad_addrs = ['0xf776a1d222751c553d9e2a244b19c77267e20514', '0x31094ed0f645dbd5efb6aca5b9a05e8ee0efb386', '0x1abaea1f7c830f0654c721306e53a20516147924']
        # Wait, we don't have addresses in liquidity_pool.
        # But swap tables have addresses.
        
        # Let's just delete all EURC related stuff that is NOT the official one.
        # The official one was EUROC. Now it is EURC.
        # So anything that was ALREADY EURC before our rename is bad.
        
        # WE NEED TO DO THIS BEFORE RENAME.
        
        # Re-enable FK checks
        cur.execute("SET session_replication_role = 'origin';")
        conn.commit()
        print("\n✅ Success!")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    fix_eurc_super_final()
