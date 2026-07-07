import psycopg2
import os
from datetime import datetime, timedelta, timezone

DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

def seed():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    print("Deleting existing PancakeSwap V3 mock data...")
    cur.execute("DELETE FROM liquidity_pool_history WHERE pool_id IN (SELECT id FROM liquidity_pool WHERE protocol = 'PancakeSwap V3')")
    cur.execute("DELETE FROM uniswap_v3_swaps WHERE protocol = 'PancakeSwap V3'")
    cur.execute("DELETE FROM liquidity_pool WHERE protocol = 'PancakeSwap V3'")
    
    pools = [
        # (coin0, coin1, fee_tier_db, fee_tier_swap, tvl, vol, txs)
        ('BNB', 'USDT', '500', '0.05%', 250000.0, 15000.0, 30),
        ('CAKE', 'USDT', '2500', '0.25%', 120000.0, 8000.0, 15),
        ('BNB', 'CAKE', '2500', '0.25%', 90000.0, 4000.0, 10),
        ('USDT', 'USDC', '100', '0.01%', 500000.0, 30000.0, 50),
    ]
    
    for c0, c1, fee_db, fee_swap, tvl, vol, txs in pools:
        pool_name = f"{c0} - {c1}"
        cur.execute("""
            INSERT INTO liquidity_pool (network, protocol, pool_name, coin0_symbol, coin1_symbol, fee_tier)
            VALUES ('BNB', 'PancakeSwap V3', %s, %s, %s, %s)
            RETURNING id
        """, (pool_name, c0, c1, fee_db))
        pool_id = cur.fetchone()[0]
        print(f"Inserted PancakeSwap V3 pool {pool_name} (ID: {pool_id})")
        
        # Insert 5 days of history
        for day in range(5):
            date_val = (datetime.now(timezone.utc) - timedelta(days=day)).date()
            cur.execute("""
                INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd, tvl_usd)
                VALUES (%s, %s, %s, %s, %s)
            """, (pool_id, date_val, txs - day, vol - day * 100, tvl + day * 500))
            
        # Insert mock swaps
        for i in range(10):
            swap_id = f"pcs_swap_{c0}_{c1}_{i}"
            timestamp = datetime.now(timezone.utc) - timedelta(hours=6 * i)
            tx_hash = f"0xpcs_mock_{c0}_{c1}_{i:04x}000000000000000000000"
            cur.execute("""
                INSERT INTO uniswap_v3_swaps (
                    id, timestamp, tx_hash, token0_address, token1_address, 
                    token0_symbol, token1_symbol, amount0, amount1, amount_usd, fee_tier, network, protocol
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'BNB', 'PancakeSwap V3')
            """, (
                swap_id,
                timestamp,
                tx_hash,
                f"0xaddr_pcs_{c0.lower()}",
                f"0xaddr_pcs_{c1.lower()}",
                c0,
                c1,
                100.0,
                100.0,
                vol / 10.0,
                fee_swap
            ))
            
    conn.commit()
    cur.close()
    conn.close()
    print("PancakeSwap V3 Seeding complete!")

if __name__ == "__main__":
    seed()
