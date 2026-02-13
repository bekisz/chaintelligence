
import os
import psycopg2
import requests
from datetime import datetime, timezone

# Configuration
DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "postgres://airflow:airflow@localhost/chaintelligence")
RPC_URL = "https://rpc.flashbots.net"

# Positions to Fix (ID -> Token ID)
TARGETS = {
    5: "111885",
    6: "112163",
    7: "111886",
    8: "111898",
    9: "111888",
    12: "112167",
    4: "112176" 
}

START_BLOCK = "0x13A5245" # Nov 2025

def rpc_call(method, params):
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    try:
        resp = requests.post(RPC_URL, json=payload, timeout=20)
        return resp.json()
    except Exception as e:
        print(f"RPC Error: {e}")
        return None

def get_creation_tx(token_id):
    tid_hex = hex(int(token_id))[2:]
    topic3 = "0x" + tid_hex.zfill(64)
    print(f"Searching for Token {token_id} (Topic: {topic3})...")
    
    params = [{"fromBlock": START_BLOCK, "toBlock": "latest", 
               "topics": ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef", 
                          "0x0000000000000000000000000000000000000000000000000000000000000000", 
                          None, topic3]}]
    data = rpc_call("eth_getLogs", params)
    if data and "result" in data and data["result"]:
        return data["result"][0]["transactionHash"], int(data["result"][0]["blockNumber"], 16)
    return None, None

def fix_metadata():
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    for pid, tid in TARGETS.items():
        try:
            tx_hash, block_num = get_creation_tx(tid)
            if not tx_hash:
                print(f"PID {pid}: No TX found.")
                continue
            
            # Get Timestamp
            ts = datetime.now(timezone.utc)
            block_data = rpc_call("eth_getBlockByNumber", [hex(block_num), False])
            if block_data and 'result' in block_data:
                ts = datetime.fromtimestamp(int(block_data['result']['timestamp'], 16), timezone.utc)
            
            print(f"PID {pid}: Found TX {tx_hash} @ {ts}")
            
            # Update DB (Only Metadata)
            cur.execute("""
                UPDATE liquidity_pool_position_event
                SET tx_hash = %s, timestamp = %s, block_number = %s
                WHERE position_id = %s AND event_type = 'create';
            """, (tx_hash, ts, block_num, pid))
            
            # If no create event, we can't insert because we don't have amounts. 
            # But the audit showed they HAVE create events (just wrong TX).
            if cur.rowcount > 0:
                print("  Updated metadata.")
            else:
                print("  No 'create' event to update.")
                
            conn.commit()
            
        except Exception as e:
            print(f"Error {pid}: {e}")
            conn.rollback()
            
    conn.close()

if __name__ == "__main__":
    fix_metadata()
