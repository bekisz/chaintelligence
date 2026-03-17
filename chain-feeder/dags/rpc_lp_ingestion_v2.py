from airflow import DAG
from airflow.sdk import task, Param
from datetime import datetime, timedelta
import logging
import os

from include.rpc_discovery_engine import RpcDiscoveryEngine

logger = logging.getLogger(__name__)

# Constants
NETWORKS = ["Ethereum"] # "Arbitrum", "Base"

@task
def determine_range_task(network, **context):
    target_address = os.getenv("TARGET_ADDRESS")
    engine = RpcDiscoveryEngine(network, target_address)
    
    current_block = engine.get_current_block()
    force_date = context["params"].get("force_start_date")
    
    var_key = f"rpc_discovery_last_block_{network}"
    # Accessing internal method or duplicated logic
    start_block = engine._resolve_start_block(var_key, current_block, force_date)
    
    # Safety check
    if start_block >= current_block:
        logger.info(f"No new blocks. Start: {start_block}, Current: {current_block}")
        return None
        
    return {"start": start_block, "end": current_block}

@task
def scan_events_task(network, range_data):
    if not range_data: return []
    
    target_address = os.getenv("TARGET_ADDRESS")
    batch_size = os.getenv("RPC_LOG_BATCH_SIZE", "2000") 
    # Engine load with batch size if specific override needed, else uses env
    engine = RpcDiscoveryEngine(network, target_address, batch_size=batch_size)
    
    start, end = range_data["start"], range_data["end"]
    
    # Log the date of the start block for verification
    try:
        b_info = engine.rpc.call_rpc("eth_getBlockByNumber", [hex(start), False])
        if b_info and "timestamp" in b_info:
            ts = int(b_info["timestamp"], 16)
            # Use standard datetime import from top of file
            from datetime import timezone
            dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            logger.info(f"Starting scan from block {start} measured at {dt_str}")
    except Exception as e:
        logger.warning(f"Could not fetch timestamp for start block {start}: {e}")

    # Process the scan in chunks to respect RPC rate limits and response sizes
    all_events = []
    chunk_size = int(batch_size)
    total_blocks = end - start
    processed = 0
    
    for s in range(start + 1, end + 1, chunk_size):
        e = min(s + chunk_size - 1, end)
        processed += (e - s + 1)
        pct = round((processed / total_blocks) * 100, 1) if total_blocks > 0 else 100
        
        logger.info(f"Scanning chunk {s}-{e} ({pct}%)")
        events = engine.scan_transfer_events(s, e)
        all_events.extend(events)
        
    logger.info(f"Scan complete. Total events found: {len(all_events)}")
    if all_events:
        logger.info(f"Events found: {all_events}")
    return all_events

@task
def enrich_details_task(network, events):
    if not events: return {}
    target_address = os.getenv("TARGET_ADDRESS")
    engine = RpcDiscoveryEngine(network, target_address)
    
    token_ids = [e['token_id'] for e in events]
    if not token_ids: return {}
    
    details = engine.fetch_onchain_details(token_ids)
    
    logger.info(f"Enrichment complete. Total details found: {len(details)}")
    if details:
         logger.info(f"Details: {details}")
         
    return details

@task
def ingest_coins_task(network, details):
    if not details: return
    target_address = os.getenv("TARGET_ADDRESS")
    engine = RpcDiscoveryEngine(network, target_address)
    engine.db.ensure_coins(details)

@task
def ingest_pools_task(network, details):
    if not details: return
    target_address = os.getenv("TARGET_ADDRESS")
    engine = RpcDiscoveryEngine(network, target_address)
    engine.db.ensure_pools(details)

@task
def ingest_positions_task(network, details, events):
    if not details: return
    target_address = os.getenv("TARGET_ADDRESS")
    engine = RpcDiscoveryEngine(network, target_address)
    # We need to map TokenID to Owner? 
    # events has 'to'/'from'.
    # We can pass events to upsert_positions helper if we modify it, 
    # or pass a map.
    # upsert_positions takes (details_map, event_list) in my implementation.
    engine.db.upsert_positions(details, events)

@task
def ingest_events_task(network, events):
    if not events: return
    target_address = os.getenv("TARGET_ADDRESS")
    engine = RpcDiscoveryEngine(network, target_address)
    engine.db.insert_events(events)

from airflow.models import Variable

@task
def update_cursor_task(network, range_data):
    if not range_data: return
    var_key = f"rpc_discovery_last_block_{network}"
    Variable.set(var_key, str(range_data["end"]))
    logger.info(f"Updated cursor for {network} to {range_data['end']}")


with DAG(
    'rpc_lp_ingestion_v2',
    default_args={'owner': 'airflow'},
    schedule=None,
    start_date=datetime.now() - timedelta(days=1),
    max_active_runs=1,
    params={
        "force_start_date": Param(None, type=["null", "string"], description="Force Start Date (YYYY-MM-DD)"),
    }
) as dag:
    
    for net in NETWORKS:
        rng = determine_range_task.override(task_id=f"determine_range_{net}")(net)
        events = scan_events_task.override(task_id=f"scan_events_{net}")(net, rng)
        details = enrich_details_task.override(task_id=f"enrich_details_{net}")(net, events)
        
        c = ingest_coins_task.override(task_id=f"ingest_coins_{net}")(net, details)
        p = ingest_pools_task.override(task_id=f"ingest_pools_{net}")(net, details)
        pos = ingest_positions_task.override(task_id=f"ingest_positions_{net}")(net, details, events)
        evt = ingest_events_task.override(task_id=f"ingest_events_{net}")(net, events)
        
        upd = update_cursor_task.override(task_id=f"update_cursor_{net}")(net, rng)
        
        # Dependencies: 
        # Range -> Scan -> Enrich -> Coins -> Pools -> Positions -> Events -> Cursor
        
        # Note: Coins depends on Enrich. Pools depends on Coins (FK?). 
        # Yes, Pool refers to Coin symbols.
        # Positions depends on Pools (FK).
        # Events depends on Positions (FK).
        
        rng >> events >> details
        details >> c >> p >> pos >> evt >> upd

