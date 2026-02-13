
import logging
import requests
import os
import psycopg2
from backfill_position_events import scan_events, DB_CONN, get_block_timestamp, RPC_LISTS

# Configure a short scan range around the missing block
MISSING_BLOCK = 24417817
START = MISSING_BLOCK - 20
END = MISSING_BLOCK + 20

def test_missing_tx():
    print(f"Testing missing TX block range: {START}-{END}")
    
    # Get positions
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    cur.execute("SELECT id, token_id, 18, 6 FROM liquidity_pool_position WHERE token_id = '1150143'") # 18/6 decimals for ETH/USDC
    row = cur.fetchone()
    if not row:
        print("Position 1150143 not found in DB")
        return
        
    positions = [{
        "id": row[0], "token_id": row[1], "network": "Ethereum", "protocol": "Uniswap V3",
        "d0": 18, "d1": 18 
    }]
    cur.close()
    conn.close()
    
    # We need to Monkey Patch the scan logic because `scan_events` in `backfill_position_events.py` calculates its own start/end based on current block.
    # Actually, I should have refactored `scan_events` to accept start/end.
    # Let's write a custom scan here using the SAME logic as backfill_position_events but hardcoded range.
    
    # Reuse valid RPCs
    network = "Ethereum"
    
    print(f"Scanning {len(positions)} positions from {START} to {END}")
    
    # Monkey patch the `current_block` logic inside `scan_events` ?? No.
    # Let's copy-paste the core scan logic.
    
    from backfill_position_events import make_rpc_request, parse_v3_log, db_insert_event, V3_MANAGER
    
    BATCH_TOPIC_LIMIT = 20
    INCREASE_LIQUIDITY_TOPIC = "0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f"
    DECREASE_LIQUIDITY_TOPIC = "0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4"
    COLLECT_TOPIC = "0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01"
    TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    # Filter IDs
    pos_map = {}
    filter_ids = []
    for p in positions:
        tid_hex = "0x" + hex(int(p['token_id']))[2:].zfill(64)
        filter_ids.append(tid_hex)
        pos_map[tid_hex.lower()] = p

    id_batches = [filter_ids[i:i + BATCH_TOPIC_LIMIT] for i in range(0, len(filter_ids), BATCH_TOPIC_LIMIT)]
    
    unique_logs = {}

    # Scan
    for id_batch in id_batches:
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getLogs",
            "params": [{
                "fromBlock": hex(START),
                "toBlock": hex(END),
                "address": V3_MANAGER,
                "topics": [
                    [INCREASE_LIQUIDITY_TOPIC, DECREASE_LIQUIDITY_TOPIC, COLLECT_TOPIC],
                    id_batch
                ]
            }],
            "id": 1
        }
        data = make_rpc_request(network, payload)
        if data and 'result' in data:
            logs = data['result']
            print(f"Found {len(logs)} logs in Batch 1")
            for l in logs:
                uid = f"{l['transactionHash']}_{l['logIndex']}"
                unique_logs[uid] = l
        else:
            print("Batch 1 returned no data or error")

    # Process
    if unique_logs:
        conn = psycopg2.connect(DB_CONN)
        cur = conn.cursor()
        sorted_logs = sorted(unique_logs.values(), key=lambda x: (int(x['blockNumber'], 16), int(x['logIndex'], 16)))
        
        for log in sorted_logs:
            topic0 = log['topics'][0]
            tx_hash = log['transactionHash']
            block = int(log['blockNumber'], 16)
            ts = parse_timestamp(network, log['blockNumber'])
            
            p = positions[0] # we know it's this one
            
            event_type = None
            if topic0 == COLLECT_TOPIC: event_type = 'collect_claim'
            elif topic0 == INCREASE_LIQUIDITY_TOPIC: event_type = 'add_liquidity'
            elif topic0 == DECREASE_LIQUIDITY_TOPIC: event_type = 'withdraw'
            
            print(f"Processing event: {event_type} at {tx_hash}")
            
            v0, v1, liq = parse_v3_log(log, event_type, p['d0'], p['d1'])
            db_insert_event(cur, p['id'], tx_hash, block, ts, event_type, v0, v1, liq)
            
        conn.commit()
        cur.close()
        conn.close()
        print("Events inserted.")
    else:
        print("No logs found.")

def parse_timestamp(network, block_hex):
    from datetime import datetime, timezone
    from backfill_position_events import get_block_timestamp
    ts_val = get_block_timestamp(network, block_hex)
    return datetime.fromtimestamp(ts_val, timezone.utc)

if __name__ == "__main__":
    test_missing_tx()
