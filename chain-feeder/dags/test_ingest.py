import sys
import os
sys.path.append(os.path.abspath('chain-feeder'))
from dotenv import load_dotenv
load_dotenv('chain-feeder/../.env.secrets')

from include.graph_discovery_client import fetch_graph_positions
from include.graph_ingestion_helpers import ingest_coins_data, ingest_pools_data, ingest_positions_data, ingest_snapshots_data
import psycopg2
import logging
logging.basicConfig(level=logging.DEBUG)

wallets = os.environ.get('TARGET_ADDRESS', '')
print(f"Target addresses: {wallets}")

print("1. Discovering positions...")
positions = fetch_graph_positions(wallets)
print(f"Found {len(positions) if positions else 0} positions")

if not positions:
    print("No positions found.")
    sys.exit(0)

# Connect to DB
conn = psycopg2.connect("postgresql://airflow:airflow@postgres:5432/chaintelligence")

print("2. Ingesting coins...")
ingest_coins_data(conn, positions)

print("3. Ingesting pools...")
ingest_pools_data(conn, positions)

print("4. Ingesting positions...")
ingest_positions_data(conn, positions)

print("5. Ingesting snapshots...")
ingest_snapshots_data(conn, positions)

conn.close()
print("Done!")
