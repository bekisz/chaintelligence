import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, '/Users/szabi/git/chaintelligence/chain-feeder/routing')
sys.path.insert(0, '/Users/szabi/git/chaintelligence/chain-feeder/include')

from postgres_fetcher import PostgresFetcher
from route_analyzer import RouteAnalyzer

fetcher = PostgresFetcher(verbose=True)
analyzer = RouteAnalyzer(verbose=True, prices={})

end_dt = datetime.now()
start_dt = end_dt - timedelta(days=4)

print("Fetching swaps...")
swaps = fetcher.fetch_swaps(start_dt, end_dt, ['USDT', 'USDC'], 'Ethereum')
print(f"Fetched {len(swaps)} swaps")

analyzer.process_batch(swaps, ['USDT'], ['USDC'])
results = analyzer.get_results()

print(f"Total TX: {results.get('total_tx')}")
for r in results.get('routes', [])[:5]:
    print(r)
