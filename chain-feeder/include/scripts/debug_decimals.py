
import psycopg2
import os

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "postgres://airflow:airflow@localhost/chaintelligence")

def debug():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # Target Position ID 48 (EURCV/EURC)
    pos_id = 48
    
    query = """
        SELECT p.id, p.token_id, pool.network, pool.protocol, 
               (SELECT decimals FROM coin WHERE symbol=pool.coin0_symbol LIMIT 1) as d0,
               (SELECT decimals FROM coin WHERE symbol=pool.coin1_symbol LIMIT 1) as d1,
               pool.id, pool.pool_name, pool.coin0_symbol, pool.coin1_symbol,
               (SELECT ethereum_address FROM coin WHERE symbol=pool.coin0_symbol LIMIT 1) as addr0,
               (SELECT ethereum_address FROM coin WHERE symbol=pool.coin1_symbol LIMIT 1) as addr1
        FROM liquidity_pool_position p
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        WHERE p.id = %s
    """
    
    cur.execute(query, (pos_id,))
    r = cur.fetchone()
    cur.close()
    conn.close()
    
    if not r:
        print("Position not found")
        return

    print(f"Data for Position {pos_id}:")
    print(f"Coin0 Symbol: {r[8]}, Decimals (d0): {r[4]}")
    print(f"Coin1 Symbol: {r[9]}, Decimals (d1): {r[5]}")
    print(f"Addr0: {r[10]}")
    print(f"Addr1: {r[11]}")
    
    addr0 = r[10]
    addr1 = r[11]
    token0_is_coin0 = True
    
    if addr0 and addr1:
        print(f"Comparing {addr0.lower()} vs {addr1.lower()}")
        if addr0.lower() > addr1.lower():
            print("addr0 > addr1. So token0_is_coin0 = False")
            token0_is_coin0 = False
        else:
             print("addr0 < addr1. So token0_is_coin0 = True")
    
    d0 = r[4] or 18
    d1 = r[5] or 18
    
    print(f"Final token0_is_coin0: {token0_is_coin0}")
    
    # Simulation
    # Assume Log Data corresponds to:
    # Amount0 (Token0) = 2e9 (EURC Raw)
    # Amount1 (Token1) = 2e21 (EURCV Raw)
    
    amount0_log = 2 * 10**9 # Approx 2000 EURC
    amount1_log = 2 * 10**21 # Approx 2000 EURCV
    
    print(f"\nSimulation with Raw Amounts: Amt0={amount0_log}, Amt1={amount1_log}")
    
    if token0_is_coin0:
        print("Logic A (token0_is_coin0=True):")
        # val0 = amt0 / d0
        val0 = amount0_log / (10**d0)
        # val1 = amt1 / d1
        val1 = amount1_log / (10**d1)
        print(f"Val0 (Coin0 {r[8]}): {val0}")
        print(f"Val1 (Coin1 {r[9]}): {val1}")
    else:
        print("Logic B (token0_is_coin0=False):")
        # amount0 is for coin1, amount1 is for coin0
        
        # val1 (Coin1) from amount0
        val1 = amount0_log / (10**d1)
        
        # val0 (Coin0) from amount1
        val0 = amount1_log / (10**d0)
        
        print(f"Val0 (Coin0 {r[8]}): {val0}")
        print(f"Val1 (Coin1 {r[9]}): {val1}")

if __name__ == "__main__":
    debug()
