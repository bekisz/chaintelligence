import asyncio
from datetime import datetime, timedelta
from api.main import resolve_token_input
from chain_feeder.routing.postgres_fetcher import PostgresFetcher
from chain_feeder.routing.route_analyzer import RouteAnalyzer
import time

start_token = "USDT"
end_token = "USDC"
start_tokens_list = resolve_token_input(start_token)
end_tokens_list = resolve_token_input(end_token)
token_filter = start_tokens_list + end_tokens_list

end_dt = datetime(2026, 7, 2, 0, 0, 0)
start_dt = end_dt - timedelta(days=1)

fetcher = PostgresFetcher(verbose=True)
analyzer = RouteAnalyzer(verbose=True)

t0 = time.time()
print(f"Fetching from {start_dt} to {end_dt}")
swaps = fetcher.fetch_swaps(start_dt, end_dt, token_filter, network="Ethereum")
t1 = time.time()
print(f"Fetched {len(swaps)} swaps in {t1-t0:.2f}s")

t2 = time.time()
analyzer.process_batch(swaps, start_tokens_list, end_tokens_list)
t3 = time.time()
print(f"Processed batch in {t3-t2:.2f}s")

results = analyzer.get_results()
t4 = time.time()
print(f"Got results in {t4-t3:.2f}s")
