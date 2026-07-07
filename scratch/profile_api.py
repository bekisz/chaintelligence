import cProfile
import pstats
import time
import sys, os
from datetime import datetime, timedelta

sys.path.append(os.path.abspath('chain-feeder/routing'))
from postgres_fetcher import PostgresFetcher
from route_analyzer import RouteAnalyzer

def run():
    fetcher = PostgresFetcher(verbose=False)
    prices = fetcher.fetch_latest_prices()
    analyzer = RouteAnalyzer(verbose=False, prices=prices)

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=7)
    
    start_tokens_list = ['USDT']
    end_tokens_list = ['USDC']
    token_filter = ['USDT', 'USDC']
    network = "Ethereum"

    print("Fetching swaps...")
    t0 = time.time()
    batch_swaps = fetcher.fetch_swaps(start_dt, end_dt, token_filter=token_filter, network=network)
    t1 = time.time()
    print(f"Fetch swaps took {t1-t0:.2f}s. Swaps count: {len(batch_swaps)}")

    print("Processing batch...")
    analyzer.process_batch(batch_swaps, start_tokens_list, end_tokens_list)
    t2 = time.time()
    print(f"Process batch took {t2-t1:.2f}s")

    pools_to_fetch = list(analyzer.get_unique_pools())
    print(f"Fetching pool stats for {len(pools_to_fetch)} pools...")
    pool_aprs = fetcher.fetch_pool_stats(pools_to_fetch, start_dt, end_dt, prices=prices)
    t3 = time.time()
    print(f"Fetch pool stats took {t3-t2:.2f}s")

if __name__ == '__main__':
    cProfile.run('run()', 'scratch/profile_stats')
    p = pstats.Stats('scratch/profile_stats')
    p.sort_stats('tottime').print_stats(10)
