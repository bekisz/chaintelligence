import sys
import os
import logging
from datetime import datetime, timedelta
import psycopg2

# Add /opt/airflow/dags to path so we can import common
sys.path.append('/opt/airflow/dags')

from common.utils.uniswap_utils import UniswapV3Fetcher

def sync_tvl_empty_only():
    logging.basicConfig(level=logging.INFO)
    logging.info("Starting Empty-Only TVL Sync...")
    
    fetcher = UniswapV3Fetcher(verbose=True)
    conn_str = os.getenv('DATA_WAREHOUSE_DB', 'postgresql://airflow:airflow@postgres:5432/chaintelligence')
    
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()
    
    # 1. Build Symbol -> Address Map
    logging.info("Building symbol->address map...")
    cur.execute("""
        SELECT DISTINCT token0_symbol, token0_address FROM uniswap_v3_swaps
        UNION 
        SELECT DISTINCT token1_symbol, token1_address FROM uniswap_v3_swaps
    """)
    symbol_map = {}
    for row in cur.fetchall():
        sym, addr = row
        if sym and addr:
            symbol_map[sym.upper()] = addr.lower()
            if len(sym) > 8:
                symbol_map[sym[:8].upper()] = addr.lower()
                
    mainnet_overrides = {
        'USDC': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
        'USDT': '0xdac17f958d2ee523a2206206994597c13d831ec7',
        'WETH': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
        'WBTC': '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599',
        'DAI': '0x6b175474e89094c44da98b954eedeac495271d0f',
        'LINK': '0x514910771af9ca656af840dff83e8264ecf986ca',
        'UNI': '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984',
        'AAVE': '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9',
        'PAXG': '0x45804880de22913dafe09f4980848ece6ecbaf78'
    }
    for sym, addr in mainnet_overrides.items():
        symbol_map[sym] = addr
                
    cur.execute("""
        SELECT lp.id, c0.symbol, c1.symbol, lp.fee_tier 
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE lp.protocol = 'Uniswap V3'
          AND lp.id NOT IN (SELECT DISTINCT pool_id FROM liquidity_pool_history)
    """)
    pools = cur.fetchall()
    logging.info(f"Found {len(pools)} Uniswap V3 pools missing daily history.")
    
    synced_count = 0
    for pool in pools:
        pool_id, c0, c1, fee = pool
        if not fee: continue
        
        addr0 = symbol_map.get(c0.upper())
        addr1 = symbol_map.get(c1.upper())
        
        if not addr0 or not addr1:
            continue
            
        try:
           if '%' in fee:
                val = float(fee.strip('%')) 
                fee_bips = int(val * 10000)
           else:
                fee_bips = int(fee)
        except:
           continue
           
        start_date = datetime.now() - timedelta(days=90)
        
        try:
            logging.info(f"Fetching data for {c0}-{c1} ({fee})...")
            data = fetcher.fetch_pool_daily_data(addr0, addr1, fee_bips, start_date)
        except Exception as e:
            logging.error(f"Error fetching data: {e}")
            continue
        
        if not data:
            continue
            
        logging.info(f"Upserting {len(data)} records for pool {c0}-{c1}")
        for d in data:
            cur.execute("""
                INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd, tvl_usd)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (pool_id, date) DO UPDATE 
                SET tvl_usd = COALESCE(liquidity_pool_history.tvl_usd, EXCLUDED.tvl_usd),
                    volume_usd = EXCLUDED.volume_usd,
                    tx_count = EXCLUDED.tx_count;
            """, (pool_id, d['date'], d['tx_count'], d['volume_usd'], d['tvl_usd']))
            
        conn.commit()
        synced_count += 1
    
    cur.close()
    conn.close()
    logging.info(f"Sync Complete. Synced {synced_count} pools.")

if __name__ == "__main__":
    sync_tvl_empty_only()
