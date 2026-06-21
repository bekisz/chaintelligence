import psycopg2
import os
from datetime import datetime, timedelta

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def seed():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # 1. Clean existing pool and history if any
    cur.execute("DELETE FROM liquidity_pool_history")
    cur.execute("DELETE FROM uniswap_v3_swaps")
    cur.execute("DELETE FROM liquidity_pool")
    
    # 2. Insert pool
    cur.execute("""
        INSERT INTO liquidity_pool (network, protocol, pool_name, coin0_symbol, coin1_symbol, fee_tier)
        VALUES ('Ethereum', 'Uniswap V3', 'EURC - EURCV', 'EURC', 'EURCV', '100')
        RETURNING id
    """)
    pool_id = cur.fetchone()[0]
    print(f"Inserted pool with ID: {pool_id}")
    
    # 3. Insert history entry (avg TVL = 100,000 USD)
    date_now = datetime.now().date()
    cur.execute("""
        INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd, tvl_usd)
        VALUES (%s, %s, %s, %s, %s)
    """, (pool_id, date_now, 10, 0.0, 100000.0))
    print("Inserted pool history entry.")
    
    # 4. Insert mock swaps (Total Volume = 50,000 USD over last 30 days)
    for i in range(10):
        swap_id = f"swap_mock_{i}"
        timestamp = datetime.now() - timedelta(days=2 * i)
        tx_hash = f"0xmockhash{i:05x}0000000000000000000000000000000"
        cur.execute("""
            INSERT INTO uniswap_v3_swaps (
                id, timestamp, tx_hash, token0_address, token1_address, 
                token0_symbol, token1_symbol, amount0, amount1, amount_usd, fee_tier
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            swap_id, 
            timestamp, 
            tx_hash, 
            '0x1abaea1f7c830bd89acc67ec4af516284b1bc33c', # EURC
            '0xdummy_eurcv', # EURCV
            'EURC', 
            'EURCV', 
            1000.0, 
            1000.0, 
            5000.0, # amount_usd
            '0.01%'
        ))
    print("Inserted 10 mock swaps.")
    
    conn.commit()
    cur.close()
    conn.close()
    print("Seeding complete!")

if __name__ == "__main__":
    seed()
