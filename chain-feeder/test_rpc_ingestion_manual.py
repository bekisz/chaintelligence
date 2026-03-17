import logging
import os
import sys

# Set up logging to stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add path to include
sys.path.append(os.path.join(os.getcwd(),'chain-feeder'))

from include.rpc_discovery_engine import RpcDiscoveryEngine

def run_test():
    # Load Env
    # We need to manually set these if running locally outside container context, 
    # but inside container they should exist.
    # Check if running outside container -> need to mock or set env manually.
    # The user is running this script via `docker exec ... python3 ...`? Or local?
    # I will assume running inside container to reuse environment variables.
    
    network = "Ethereum"
    target_address = os.getenv("TARGET_ADDRESS")
    print(f"Target Address: {target_address}")
    
    engine = RpcDiscoveryEngine(network, target_address)
    
    current_block = engine.get_current_block()
    print(f"Current Block: {current_block}")
    
    # Try scanning last 1000 blocks
    start = current_block - 10000 
    end = current_block
    
    print(f"Scanning range {start} to {end}...")
    events = engine.scan_transfer_events(start, end)
    
    print(f"Found {len(events)} events.")
    for e in events:
        print(f"Event: {e}")
        
    if not events:
        print("No events found. Trying a wider historical search if possible? Or maybe wallet has no recent activity.")
        return

    # Test Enrichment
    print("Testing Enrichment...")
    token_ids = [e['token_id'] for e in events]
    details = engine.fetch_onchain_details(token_ids)
    print(f"Details resolved for {len(details)} tokens.")
    
    # Test Ingestion
    print("Testing DB Ingestion (Coins)...")
    engine.db.ensure_coins(details)
    
    print("Testing DB Ingestion (Pools)...")
    engine.db.ensure_pools(details)
    
    print("Testing DB Ingestion (Positions)...")
    engine.db.upsert_positions(details, events)
    
    print("Testing DB Ingestion (Events)...")
    engine.db.insert_events(events)
    
    print("Done.")

if __name__ == "__main__":
    run_test()
