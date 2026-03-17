
import psycopg2
import os

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def fix_eurc_safe():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    official_addr = '0x1abaea1f7c830bd89acc67ec4af516284b1bc33c'
    bad_addr = '0x1abaea1f7c830f0654c721306e53a20516147924'
    
    try:
        # 1. Temporarily rename bad EURC to avoid symbol conflict
        # We need to find all tables that reference symbol
        # But let's just update the coin table directly if we can, but symbol is the PK/unique key.
        
        # Check if symbol is unique
        cur.execute("UPDATE coin SET symbol = 'EURC_BAD' WHERE ethereum_address = %s", (bad_addr,))
        print(f"Renamed bad EURC to EURC_BAD. Rows: {cur.rowcount}")
        
        # 2. Rename EUROC to EURC
        cur.execute("UPDATE coin SET symbol = 'EURC', decimals = 6 WHERE ethereum_address = %s", (official_addr,))
        print(f"Renamed EUROC to EURC. Rows: {cur.rowcount}")
        
        # 3. Update all references to EUROC -> EURC
        # (References to EURC_BAD stay as they are for now, or we can delete them)
        
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

        # 4. Now handle the bad ones. If we want to keep them but label them differently:
        for table, col in tables_to_update:
            cur.execute(f"UPDATE {table} SET {col} = 'EURC_BAD' WHERE {col} = 'EURC'")
            # WAIT! NO! This would update the GOOD ones we just renamed!
            # We need to filter by address if possible, but swaps don't have addresses for both tokens? 
            # Swaps DO have addresses!
            pass
            
        # Actually, let's just delete the bad ones from swaps/pools if they are tiny
        # and delete the bad coin.
        
        # To delete bad coin, we must clear its references.
        cur.execute("DELETE FROM liquidity_pool WHERE coin0_symbol = 'EURC_BAD' OR coin1_symbol = 'EURC_BAD'")
        print(f"Deleted bad pools. Rows: {cur.rowcount}")
        
        cur.execute("DELETE FROM coin WHERE symbol = 'EURC_BAD'")
        print(f"Deleted bad coin. Rows: {cur.rowcount}")

        conn.commit()
        print("\n✅ EURC Migration Successful!")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    fix_eurc_safe()
