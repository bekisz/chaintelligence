
import psycopg2
import os

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def fix_eurc():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    print("Fetching current EURC/EUROC status...")
    cur.execute("SELECT symbol, decimals, ethereum_address FROM coin WHERE symbol IN ('EURC', 'EUROC')")
    rows = cur.fetchall()
    print(f"Current coins: {rows}")
    
    official_addr = '0x1abaea1f7c830bd89acc67ec4af516284b1bc33c'
    bad_addr = '0x1abaea1f7c830f0654c721306e53a20516147924'
    
    # 1. Delete the "bad" EURC entry if it exists
    cur.execute("DELETE FROM coin WHERE ethereum_address = %s", (bad_addr,))
    print(f"Deleted bad EURC entry at {bad_addr} (if existed). Rows affected: {cur.rowcount}")
    
    # 2. Rename EUROC to EURC and set decimals to 6
    cur.execute("""
        UPDATE coin 
        SET symbol = 'EURC', decimals = 6 
        WHERE ethereum_address = %s
    """, (official_addr,))
    print(f"Renamed EUROC to EURC for official address {official_addr}. Rows affected: {cur.rowcount}")
    
    # 3. Update liquidity_pool table
    cur.execute("UPDATE liquidity_pool SET coin0_symbol = 'EURC' WHERE coin0_symbol = 'EUROC'")
    print(f"Updated liquidity_pool coin0_symbol. Rows: {cur.rowcount}")
    cur.execute("UPDATE liquidity_pool SET coin1_symbol = 'EURC' WHERE coin1_symbol = 'EUROC'")
    print(f"Updated liquidity_pool coin1_symbol. Rows: {cur.rowcount}")
    
    # 4. Update swaps tables
    cur.execute("UPDATE uniswap_v3_swaps SET token0_symbol = 'EURC' WHERE token0_symbol = 'EUROC'")
    print(f"Updated uniswap_v3_swaps token0_symbol. Rows: {cur.rowcount}")
    cur.execute("UPDATE uniswap_v3_swaps SET token1_symbol = 'EURC' WHERE token1_symbol = 'EUROC'")
    print(f"Updated uniswap_v3_swaps token1_symbol. Rows: {cur.rowcount}")
    
    cur.execute("UPDATE uniswap_v4_swaps SET token0_symbol = 'EURC' WHERE token0_symbol = 'EUROC'")
    print(f"Updated uniswap_v4_swaps token0_symbol. Rows: {cur.rowcount}")
    cur.execute("UPDATE uniswap_v4_swaps SET token1_symbol = 'EURC' WHERE token1_symbol = 'EUROC'")
    print(f"Updated uniswap_v4_swaps token1_symbol. Rows: {cur.rowcount}")
    
    # 5. Update coin_price_history
    cur.execute("UPDATE coin_price_history SET symbol = 'EURC' WHERE symbol = 'EUROC'")
    print(f"Updated coin_price_history. Rows: {cur.rowcount}")
    
    # 6. Optional: Update pool_name in liquidity_pool
    cur.execute("UPDATE liquidity_pool SET pool_name = REPLACE(pool_name, 'EUROC', 'EURC') WHERE pool_name LIKE '%EUROC%'")
    print(f"Updated pool_name in liquidity_pool. Rows: {cur.rowcount}")

    conn.commit()
    cur.close()
    conn.close()
    print("\n✅ EURC Fix Complete!")

if __name__ == "__main__":
    fix_eurc()
