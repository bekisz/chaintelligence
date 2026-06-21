import sys
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

# Set host to localhost:5433 for local execution
os.environ['DATA_WAREHOUSE_DB'] = "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433"

# Add path to sys.path
sys.path.insert(0, '/Users/szabi/git/chaintelligence/chain-feeder/routing')

from postgres_fetcher import PostgresFetcher

fetcher = PostgresFetcher(verbose=True)
start_date = datetime(2026, 4, 14, tzinfo=timezone.utc)
end_date = datetime(2026, 4, 21, tzinfo=timezone.utc)

res = fetcher.fetch_pool_stats([('USDC', 'WETH', '838.8608%|v4')], start_date, end_date)
print("Result:", res)
