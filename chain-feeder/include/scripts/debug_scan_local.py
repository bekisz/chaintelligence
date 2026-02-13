import logging
import requests
import time
import os
from datetime import datetime, timezone
from collections import defaultdict

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
V3_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
V4_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
V4_CORE_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
V4_MODIFY_TOPIC = "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

RPC_URL = "https://rpc.ankr.com/eth/YOUR_RPC_KEY"

def fetch_tx_receipt_transfers(tx_hash, from_address, min_log_index):
    try:
        payload = {"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[tx_hash],"id":1}
        resp = requests.post(RPC_URL, json=payload, timeout=5)
        data = resp.json()
        if 'result' not in data: return []
        
        logs = data['result']['logs']
        transfers = []
        logs.sort(key=lambda x: int(x['logIndex'], 16))
        
        for log in logs:
            idx = int(log['logIndex'], 16)
            if idx <= min_log_index: continue
            
            if len(log['topics']) > 0 and log['topics'][0] == V4_MODIFY_TOPIC:
                if log['address'].lower() == V4_CORE_MANAGER.lower(): break
            
            if len(log['topics']) > 0 and log['topics'][0] == TRANSFER_TOPIC:
                if len(log['topics']) > 1:
                    sender = "0x" + log['topics'][1][26:] 
                    if sender.lower() == from_address.lower():
                        amt = int(log['data'], 16)
                        transfers.append({"token": log['address'], "amount": amt})
        return transfers
    except Exception as e:
        print(f"Receipt fetch error: {e}")
        return []

def run():
    # Target Range: 24207520 to 24207540 (Event at 24207528)
    start = 24207520
    end = 24207540
    
    # Target Pool/Token
    pool_id = "0x00b9edc1583bf6ef09ff3a09f6c23ecb57fd7d0bb75625717ec81eed181e22d7"
    token_id = 103718
    
    topic0 = V4_MODIFY_TOPIC
    topic1 = [pool_id]
    topic2 = "0x" + "0"*24 + V4_MANAGER[2:].lower()
    
    topics = [topic0, topic1, topic2]
    
    print(f"Filtering logic:")
    print(f"Topic0: {topic0}")
    print(f"Topic1: {topic1}")
    print(f"Topic2: {topic2}")
    
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(start),
            "toBlock": hex(end),
            "address": V4_CORE_MANAGER,
            "topics": topics
        }],
        "id": 1
    }
    
    print("Sending ETH_GETLOGS...")
    resp = requests.post(RPC_URL, json=payload)
    print(f"Response Status: {resp.status_code}")
    data = resp.json()
    logs = data.get('result', [])
    print(f"Logs found: {len(logs)}")
    
    for log in logs:
        print(f"\nProcessing Log {log['transactionHash']}...")
        print(f"Topic 1 (PoolID): {log['topics'][1]}")
        
        # Check Pool ID Match manually
        if log['topics'][1].lower() == pool_id.lower():
            print("Pool Match CONFIRMED.")
        else:
            print(f"Pool Match FAILED: {log['topics'][1]} vs {pool_id}")
            
        # Parse Data
        data_hex = log['data'][2:]
        salt_hex = data_hex[192:256]
        salt_id = int(salt_hex, 16)
        print(f"Salt ID: {salt_id}")
        
        if str(salt_id) == str(token_id):
            print("Token ID Match CONFIRMED.")
            
            # Fetch Receipt
            tx_transfers = fetch_tx_receipt_transfers(log['transactionHash'], V4_CORE_MANAGER, int(log['logIndex'], 16))
            print(f"Transfers found: {len(tx_transfers)}")
            for t in tx_transfers:
                print(f" - {t['token']} : {t['amount']}")
        else:
            print(f"Token ID Match FAILED: {salt_id} vs {token_id}")

if __name__ == "__main__":
    run()
