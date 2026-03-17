
import sys
import os
from datetime import datetime, timedelta

# Add routing to path
sys.path.append('/Users/szablocsbeki/git/chaintelligence/chain-feeder/routing')

from postgres_fetcher import PostgresFetcher
from route_analyzer import RouteAnalyzer

def test_new_results():
    fetcher = PostgresFetcher(verbose=True)
    analyzer = RouteAnalyzer(verbose=True)
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    
    print(f"Fetching swaps for EURC from {start_date} to {end_date}...")
    swaps = fetcher.fetch_swaps(start_date, end_date, token_filter=['EURC'])
    
    print(f"Analyzing {len(swaps)} swaps...")
    results = analyzer.analyze_routes(swaps, ['EURC'], ['EURCV', 'USDC', 'USDT'])
    
    # Enrichment step (mocking API enrichment)
    pools_to_fetch = []
    for r in results['routes']:
        path = r['path_tokens']
        for i in range(0, len(path)-2, 2):
            pools_to_fetch.append((path[i], path[i+2], path[i+1]))
    
    aprs = fetcher.fetch_pool_stats(pools_to_fetch, start_date, end_date, prices=analyzer.prices)
    
    print("\nTarget Route Results:")
    for r in results['routes']:
        if 'EURCV' in r['path']:
            path = r['path_tokens']
            # Enrichment
            leg_aprs = []
            for i in range(0, len(path)-2, 2):
                key = f"{path[i]}-{path[i+2]}-{path[i+1]}"
                apr = aprs.get(key)
                if apr: leg_aprs.append(apr)
            
            route_apr = sum(leg_aprs)/len(leg_aprs) if leg_aprs else 0
            print(f"{r['path']}: {r['count']} txs, ${r['volume']:,.2f} volume, APR: {route_apr:.2%}")
    
    print("\nTop 3 Routes Overall:")
    for r in results['routes'][:5]:
        print(f"{r['path']}: {r['count']} txs, ${r['volume']:,.2f} volume")
    
    print(f"\nTotal Volume: ${results['total_volume']:,.2f}")
    print(f"Total TXs: {results['total_tx']}")

if __name__ == "__main__":
    test_new_results()
