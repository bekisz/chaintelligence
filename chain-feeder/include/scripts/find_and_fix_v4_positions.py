
import os
import time
import requests
import psycopg2
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

# Known V4 Start Block (Approx)
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
    # Hex token ID padded to 32 bytes
    tid_hex = hex(int(token_id))[2:]
    topic3 = "0x" + tid_hex.zfill(64)
    
    print(f"Searching for Token {token_id} (Topic: {topic3})...")
    
    # 0xddf252... is Transfer
    # Topic 1 (From) = 0x0...0 (Mint)
    # Topic 3 (TokenId) = token_id
    
    params = [{
        "fromBlock": START_BLOCK,
        "toBlock": "latest",
        "topics": [
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
            "0x0000000000000000000000000000000000000000000000000000000000000000",
            None,
            topic3
        ]
    }]
    
    data = rpc_call("eth_getLogs", params)
    if data and "result" in data:
        logs = data["result"]
        if logs:
            # Return the first one (creation)
            return logs[0]["transactionHash"], int(logs[0]["blockNumber"], 16)
            
    print("  No creation log found.")
    return None, None

def calculate_amounts(tx_hash, dec0, dec1):
    print(f"  Calculating amounts for TX {tx_hash}...")
    
    # Get Logs for TX
    data = rpc_call("eth_getTransactionReceipt", [tx_hash])
    logs = []
    
    if data and "result" in data and data["result"]:
        logs = data["result"]["logs"]
    else:
        print("  Receipt failed, trying getTransaction to get block logs...")
        tx_data = rpc_call("eth_getTransactionByHash", [tx_hash])
        if tx_data and "result" in tx_data:
             block = tx_data["result"]["blockNumber"]
             logs_data = rpc_call("eth_getLogs", [{"fromBlock": block, "toBlock": block}])
             if logs_data and "result" in logs_data:
                 logs = [l for l in logs_data["result"] if l["transactionHash"] == tx_hash]
    
    if not logs:
        return 0, 0, "No Logs"

    # Known Pool Managers / Hubs
    MANAGERS = [
        "0x000000000004444c5dc75cb358380d2e3de08a90",
        "0xbbbba1ee822c9b8fc134dea6adfc26603a9cbbbb" 
    ]
    
    token_deltas = {} # Token Addr -> Net Amount
    
    for l in logs:
        # Transfer Event
        if l['topics'][0] == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef' and len(l['topics']) == 3:
            src = '0x' + l['topics'][1][26:]
            dst = '0x' + l['topics'][2][26:]
            val = int(l['data'], 16)
            token = l['address'].lower()
            
            # Filter out the NFT token itself (Position Manager)
            # If the token address IS one of the managers, it's likely the NFT transfer.
            if token in [m.lower() for m in MANAGERS]:
                continue
                
            is_input = any(dst.lower() == m.lower() for m in MANAGERS)
            is_refund = any(src.lower() == m.lower() for m in MANAGERS)
            
            if is_input:
                token_deltas[token] = token_deltas.get(token, 0) + val
            elif is_refund:
                token_deltas[token] = token_deltas.get(token, 0) - val
                
    # Now map to amount0/amount1 based on address sort order
    found_tokens = sorted(token_deltas.keys())
    print(f"  Found Tokens: {found_tokens}")
    
    val0 = 0
    val1 = 0
    
    if len(found_tokens) >= 1:
        val0 = token_deltas[found_tokens[0]]
    if len(found_tokens) >= 2:
        val1 = token_deltas[found_tokens[1]]
        
    return val0 / (10**dec0), val1 / (10**dec1), "Success"

def fix_all():
    conn = psycopg2.connect(DB_CONN)
    
    for pid, tid in TARGETS.items():
        try:
            print(f"\n--- Processing PID {pid} (TID {tid}) ---")
            cur = conn.cursor()
            
            # 1. Get Pool Info (Decimals only now)
            cur.execute("""
                SELECT c0.decimals, c1.decimals 
                FROM liquidity_pool_position p 
                JOIN liquidity_pool pool ON p.pool_id = pool.id
                JOIN coin c0 ON pool.coin0_symbol = c0.symbol
                JOIN coin c1 ON pool.coin1_symbol = c1.symbol
                WHERE p.id = %s
            """, (pid,))
            pool_info = cur.fetchone()
            if not pool_info:
                print("  Pool info not found.")
                continue
                
            dec0, dec1 = pool_info[0], pool_info[1]
            
            # 2. Find Creation TX
            tx_hash, block_num = get_creation_tx(tid)
            if not tx_hash:
                print(f"Skipping {pid} (No TX found)")
                continue
            
            print(f"  TX: {tx_hash} (Block {block_num})")
                
            # 3. Calculate Amounts
            amt0, amt1, status = calculate_amounts(tx_hash, dec0, dec1)
            print(f"  Calculated: {amt0} / {amt1} ({status})")
            
            if amt0 <= 0 and amt1 <= 0:
                print("  WARN: Zero amounts. Skipping update.")
                continue
                
            # 4. Update DB
            ts_data = rpc_call("eth_getBlockByNumber", [hex(block_num), False])
            ts_val = int(ts_data['result']['timestamp'], 16)
            ts_iso = datetime.fromtimestamp(ts_val, timezone.utc)
            
            print("  UPDATING DB...")
            
            # Delete old create if exists? Or Update.
            cur.execute("""
                UPDATE liquidity_pool_position_event
                SET amount0 = %s, amount1 = %s, timestamp = %s, tx_hash = %s
                WHERE position_id = %s AND event_type = 'create'
            """, (amt0, amt1, ts_iso, tx_hash, pid))
            
            if cur.rowcount == 0:
                print("  Inserting new event...")
                cur.execute("""
                    INSERT INTO liquidity_pool_position_event 
                    (position_id, tx_hash, block_number, timestamp, event_type, amount0, amount1, liquidity_change)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 0)
                """, (pid, tx_hash, block_num, ts_iso, 'create', amt0, amt1))
                
            conn.commit()
            print("  Success.")
            cur.close()
            
        except Exception as e:
            print(f"Error processing {pid}: {e}")
            conn.rollback()
            
    conn.close()

if __name__ == "__main__":
    fix_all()
