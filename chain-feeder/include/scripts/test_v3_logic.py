import requests
import json
import logging

# New V3 Topic
COLLECT_TOPIC = "0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01"
V3_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
RPC_URL = "https://rpc.ankr.com/eth/YOUR_RPC_KEY"

def test():
    block = 24417817
    # Target Token ID (hex)
    tid = 1150143
    tid_hex = hex(tid) # 0x118cbf
    padded_tid = "0x" + "0"*64
    padded_tid = padded_tid[:66-len(hex(tid)[2:])] + hex(tid)[2:] # Pad to 32 bytes
    
    # Actually, eth_getLogs topics are 32 bytes.
    # tid_hex from log was '0x000...0118cbf'.
    topic_tid = "0x" + "0"*(64-len(hex(tid)[2:])) + hex(tid)[2:]
    
    topics = [COLLECT_TOPIC, topic_tid]
    
    print(f"Querying Block {block} for V3 Collect...")
    print(f"Address: {V3_MANAGER}")
    print(f"Topics: {topics}")
    
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(block),
            "toBlock": hex(block),
            "address": V3_MANAGER,
            "topics": topics
        }],
        "id": 1
    }
    
    resp = requests.post(RPC_URL, json=payload)
    logs = resp.json().get('result', [])
    print(f"Logs found: {len(logs)}")
    
    for log in logs:
        print(f"Log: {log['transactionHash']}")
        data_hex = log['data'][2:]
        print(f"Data Len: {len(data_hex)}")
        
        # Test my parsing logic
        if len(data_hex) < 192: 
            print("Skipped (len < 192)")
            continue
            
        amt0 = int(data_hex[64:128], 16)
        amt1 = int(data_hex[128:192], 16)
        print(f"Parsed Amt0: {amt0}")
        print(f"Parsed Amt1: {amt1}")

if __name__ == "__main__":
    test()
