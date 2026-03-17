
import sys
import os
from datetime import datetime, timedelta

# Add routing to path
sys.path.append('/Users/szablocsbeki/git/chaintelligence/chain-feeder/routing')
from postgres_fetcher import PostgresFetcher

def debug_fetcher():
    fetcher = PostgresFetcher(verbose=True)
    
    # Pool for EURCV-EURC 0.01% V3
    # Based on our previous check, this is Pool 1748
    pools = [('EURC', 'EURCV', '0.01%|v3')]
    
    # Try a 1-day window
    end_date = datetime.now()
    start_date = end_date - timedelta(days=1)
    
    print(f"Calling fetch_pool_stats for {pools} from {start_date} to {end_date}...")
    results = fetcher.fetch_pool_stats(pools, start_date, end_date, prices={})
    
    print(f"Results: {results}")

if __name__ == "__main__":
    debug_fetcher()
