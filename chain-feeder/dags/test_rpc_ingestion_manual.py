import logging
import os
import sys

# Set up logging to stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add Airflow root to path to find 'include'
sys.path.append('/opt/airflow')

from include.rpc_discovery_engine import RpcDiscoveryEngine

def run_test():
    network = "Ethereum"
    target_address = os.getenv("TARGET_ADDRESS")
    print(f"Target Address: {target_address}")
    
    try:
        engine = RpcDiscoveryEngine(network, target_address)
    except Exception as e:
        print(f"Failed to init engine: {e}")
        return
    
    current_block = engine.get_current_block()
    print(f"Current Block: {current_block}")
    
    if current_block == 0:
        print("Failed to get current block from RPC.")
        return

    # Try last 1000 blocks to confirm logic works
    # If no transactions in recent blocks, we can try widening or specific range known to have events.
    # E.g. try recent 20k blocks.
    # Or force a known transaction hash logic.
    
    start = current_block - 20000
    end = current_block
    chunk = 1000
    
    print(f"Scanning range {start} to {end} in chunks of {chunk}...")
    
    all_events = []
    for s in range(start, end, chunk):
        e = min(s + chunk - 1, end)
        print(f"  Scanning {s}-{e}")
        evts = engine.scan_transfer_events(s, e)
        if evts:
            print(f"  Found {len(evts)} events in chunk.")
            all_events.extend(evts)
            
    events = all_events
    
    print(f"Found {len(events)} total events.")
    for e in events:
        print(f"Event: {e}")
        
    if not events:
        print("No events found in recent range.")
        return

    # Test Enrichment
    print("Testing Enrichment...")
    token_ids = [e['token_id'] for e in events]
    details = engine.fetch_onchain_details(token_ids)
    print(f"Details resolved for {len(details)} tokens.")
    
    # Test Ingestion
    print("Testing DB Ingestion (Coins)...")
    try:
        engine.db.ensure_coins(details)
        print("Coins OK.")
    except Exception as e:
        print(f"Coins Failed: {e}")
    
    print("Testing DB Ingestion (Pools)...")
    try:
        engine.db.ensure_pools(details)
        print("Pools OK.")
    except Exception as e:
         print(f"Pools Failed: {e}")
    
    print("Testing DB Ingestion (Positions)...")
    try:
        engine.db.upsert_positions(details, events)
        print("Positions OK.")
    except Exception as e:
         print(f"Positions Failed: {e}")
    
    print("Testing DB Ingestion (Events)...")
    try:
        engine.db.insert_events(events)
        print("Events OK.")
    except Exception as e:
         print(f"Events Failed: {e}")
    
    print("Done.")

if __name__ == "__main__":
    run_test()
