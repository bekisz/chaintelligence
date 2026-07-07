import psycopg2
import sys

DATA_WAREHOUSE_DB = "dbname=chaintelligence user=chaintelligence password=chaintelligence host=localhost port=5433"
conn = psycopg2.connect(DATA_WAREHOUSE_DB)
cur = conn.cursor()
cur.execute("SELECT network, pool_address, protocol, coin0_symbol, coin1_symbol, fee_tier FROM liquidity_pool WHERE network='Arbitrum' AND coin0_symbol='WBTC' AND protocol='Uniswap V3' LIMIT 5")
for r in cur.fetchall():
    print(r)

cur.close()
conn.close()
