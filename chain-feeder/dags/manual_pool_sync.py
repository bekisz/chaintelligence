import sys
import os
import logging
from datetime import datetime, timedelta
import psycopg2
from airflow.providers.postgres.hooks.postgres import PostgresHook

logging.basicConfig(level=logging.INFO)

HARDNESS_MAP = {
    'USDC': 1000, 'USDT': 990, 'DAI': 970, 'GHO': 950,
    'WBTC': 870, 'WETH': 860, 'ETH': 860,
    'LINK': 850, 'UNI': 840, 'AAVE': 820
}

def get_base_asset_order(sym0, sym1):
    h0 = HARDNESS_MAP.get(sym0, 0)
    h1 = HARDNESS_MAP.get(sym1, 0)
    is_swapped = False
    if h0 > h1: is_swapped = True
    elif h0 == h1 and sym0 > sym1: is_swapped = True
    if is_swapped: return sym1, sym0
    else: return sym0, sym1

def normalize_fee_tier(fee_str):
    if not fee_str: return None
    if fee_str.isdigit(): return fee_str
    mapping = {
        '0.01%': '100',
        '0.05%': '500',
        '0.3%': '3000',
        '1.0%': '10000'
    }
    return mapping.get(fee_str.strip(), fee_str)

def sync_pools():
    conn_str = os.getenv('DATA_WAREHOUSE_DB', 'postgresql://airflow:airflow@postgres:5432/chaintelligence')
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    
    logging.info("Fetching allowed coins...")
    cur.execute("SELECT symbol FROM coin")
    allowed_coins = set(row[0] for row in cur.fetchall())
    
    logging.info("Scanning swaps table for new pools...")
    cur.execute("SELECT DISTINCT token0_symbol, token1_symbol, fee_tier FROM uniswap_v3_swaps")
    rows = cur.fetchall()
    
    new_pools = 0
    for r in rows:
        s0, s1, fee = r
        if not s0 or not s1: continue
        s0_norm = s0[:8].upper()
        s1_norm = s1[:8].upper()
        
        if s0_norm not in allowed_coins or s1_norm not in allowed_coins:
            continue
            
        c0, c1 = get_base_asset_order(s0_norm, s1_norm)
        pool_name = f"{c0} - {c1}"
        fee_bips = normalize_fee_tier(fee)

        try:
            cur.execute("""
                INSERT INTO liquidity_pool (network, protocol, pool_name, coin0_symbol, coin1_symbol, fee_tier)
                VALUES ('Ethereum', 'Uniswap V3', %s, %s, %s, %s)
                ON CONFLICT (network, protocol, pool_name, fee_tier) DO NOTHING
            """, (pool_name, c0, c1, fee_bips))
            if cur.statusmessage.startswith("INSERT 0 1"):
                new_pools += 1
                print(f"Created pool: {pool_name} ({fee_bips})")
        except Exception as e:
            conn.rollback()
            print(f"Error: {e}")
            
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Synced {new_pools} new pools.")

if __name__ == "__main__":
    sync_pools()
