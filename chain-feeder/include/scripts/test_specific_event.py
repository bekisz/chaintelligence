
import logging
import requests
import os
import psycopg2
from collections import defaultdict
from web3 import Web3

# Reuse logic from backfill_position_events
from backfill_position_events import scan_events, get_rpc, DB_CONN

# Config
V3_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"

def test_specific_tx():
    # Target TX Block
    TARGET_BLOCK = 24417817
    START = TARGET_BLOCK - 5
    END = TARGET_BLOCK + 5
    
    # Get positions
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT p.id, p.token_id, pool.network, pool.protocol, 
               (SELECT decimals FROM coin WHERE symbol=pool.coin0_symbol LIMIT 1) as d0,
               (SELECT decimals FROM coin WHERE symbol=pool.coin1_symbol LIMIT 1) as d1
        FROM liquidity_pool_position p
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        WHERE p.token_id = '1150143' -- The token ID from the missing TX log
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    positions = []
    for r in rows:
        positions.append({
            "id": r[0], "token_id": r[1], "network": r[2], "protocol": r[3],
            "d0": r[4] or 18, "d1": r[5] or 18
        })
        
    print(f"Found {len(positions)} positions for Token ID 1150143")
    if not positions:
        print("Position not found in DB! Cannot scan.")
        return

    rpcs = [
        "https://rpc.ankr.com/eth",
        "https://cloudflare-eth.com", 
        "https://eth.llamarpc.com"
    ]
    
    # Minimal Scan
    print(f"Scanning block {START} to {END}...")
    
    # Topics
    INCREASE_LIQUIDITY_TOPIC = "0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f"
    DECREASE_LIQUIDITY_TOPIC = "0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4"
    COLLECT_TOPIC = "0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01"
    
    tid_hex = "0x" + hex(int(1150143))[2:].zfill(64)
    
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(START),
            "toBlock": hex(END),
            "address": V3_MANAGER,
            "topics": [
                [INCREASE_LIQUIDITY_TOPIC, DECREASE_LIQUIDITY_TOPIC, COLLECT_TOPIC],
                tid_hex
            ]
        }],
        "id": 1
    }
    
    logs = []
    for rpc in rpcs:
        print(f"Trying RPC: {rpc}")
        try:
            resp = requests.post(rpc, json=payload, timeout=10)
            if resp.status_code == 200:
                logs = resp.json().get('result', [])
                break
            else:
                 print(f"Error {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            print(f"Error: {e}")
    print(f"Found {len(logs)} logs.")
    
    for l in logs:
        print(f"Log: {l['topics'][0]} in {l['transactionHash']}")
        
        # Parse data
        data_hex = l['data'][2:]
        if len(data_hex) >= 192:
             v0 = int(data_hex[64:128], 16)
             v1 = int(data_hex[128:192], 16)
             print(f"  Parsed Amounts: {v0} / {v1}")


if __name__ == "__main__":
    # Ensure PYTHONPATH includes current dir for import
    # sys.path.append(os.getcwd())
    test_specific_tx()
