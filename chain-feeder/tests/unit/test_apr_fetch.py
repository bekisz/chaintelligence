import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from routing.postgres_fetcher import PostgresFetcher
from datetime import datetime, timedelta

def test_fetch_stats():
    fetcher = PostgresFetcher(verbose=True)
    
    # Define a known pool from the screenshot/data
    # USDC -> USDT. Fee? Likely 0.05% (500) or 0.01% (100).
    pool_05 = ('USDC', 'USDT', '0.05%')
    pool_01 = ('USDC', 'USDT', '0.01%')
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    
    print(f"Fetching stats for {pool_05} and {pool_01}...")
    try:
        results = fetcher.fetch_pool_stats([pool_05, pool_01], start_date, end_date)
        print("Results:", results)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test_fetch_stats()
