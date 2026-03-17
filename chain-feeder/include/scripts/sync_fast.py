import sys, os, psycopg2
from datetime import datetime, timedelta
sys.path.insert(0, '/app/chain-feeder/dags')
from common.utils.uniswap_utils import UniswapV3Fetcher
from dotenv import load_dotenv

load_dotenv('/app/.env.secrets')
DATA_WAREHOUSE_DB = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=postgres port=5432')

fetcher = UniswapV3Fetcher(verbose=True)
conn = psycopg2.connect(DATA_WAREHOUSE_DB)
cur = conn.cursor()

cur.execute("SELECT symbol, ethereum_address FROM coin WHERE symbol IN ('USDC', 'USDT', 'WETH')")
symbol_map = {row[0]: row[1].lower() for row in cur.fetchall()}

cur.execute("SELECT id, coin0_symbol, coin1_symbol, fee_tier FROM liquidity_pool WHERE coin0_symbol IN ('USDC', 'USDT', 'WETH') AND coin1_symbol IN ('USDC', 'USDT', 'WETH')")
pools = cur.fetchall()

start_date = datetime.now() - timedelta(days=90)

for pool in pools:
    pool_id, c0, c1, fee = pool
    addr0 = symbol_map.get(c0)
    addr1 = symbol_map.get(c1)
    if not addr0 or not addr1 or not fee: continue
    
    data = fetcher.fetch_pool_daily_data(addr0, addr1, int(fee), start_date)
    if data:
        print(f"Updating {len(data)} records for {c0}-{c1} ({fee})")
        for d in data:
            cur.execute("""
                INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd, tvl_usd)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (pool_id, date) DO UPDATE 
                SET tvl_usd = EXCLUDED.tvl_usd, volume_usd = EXCLUDED.volume_usd, tx_count = EXCLUDED.tx_count;
            """, (pool_id, d['date'], d['tx_count'], d['volume_usd'], d['tvl_usd']))
        conn.commit()

print("Done")
