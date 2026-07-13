import os
import sys
from datetime import datetime, timedelta

# Add chain-feeder/routing to path
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'chain-feeder'))

from routing.postgres_fetcher import PostgresFetcher

def main():
    fetcher = PostgresFetcher(verbose=True)
    
    # 30-day range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    
    # We want to fetch stats for the three previously glitched pools:
    # 1. BTT-WETH on Ethereum (Uniswap V3)
    # 2. KITE-USDC on Ethereum (Uniswap V4)
    # 3. MUSD-USDT on Ethereum (Uniswap V4)
    pools = [
        ['BTT', 'WETH', '3000|Uniswap V3|Ethereum'],
        ['KITE', 'USDC', '100|Uniswap V4|Ethereum'],
        ['MUSD', 'USDT', '9000|Uniswap V4|Ethereum']
    ]
    
    print("Fetching pool stats...")
    results = fetcher.fetch_pool_stats(pools, start_date, end_date)
    
    print("\n--- RESULTS ---")
    for k, stat in results.items():
        print(f"Pool Key: {k}")
        print(f"  TVL: {stat.get('tvl')}")
        print(f"  APR: {stat.get('apr')}")

if __name__ == "__main__":
    main()
