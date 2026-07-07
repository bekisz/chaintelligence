import sys, os
from datetime import datetime, timedelta
sys.path.append(os.path.abspath('chain-feeder/routing'))
from postgres_fetcher import PostgresFetcher

fetcher = PostgresFetcher(verbose=True)

# Fake pools to test
pools = [
    ['USDT', 'USDC', '0.05%|Uniswap V3|Ethereum'],
    ['USDT', 'USDC', '0.01%|Uniswap V3|Ethereum'],
    ['USDC', 'WETH', '0.05%|Uniswap V3|Arbitrum']
]
end_dt = datetime.now()
start_dt = end_dt - timedelta(days=7)

prices = fetcher.fetch_latest_prices()
import time
start_t = time.time()
res = fetcher.fetch_pool_stats(pools, start_dt, end_dt, prices)
dur = time.time() - start_t
print(f"Original fetch_pool_stats took {dur:.2f} seconds. Res: {res}")
