
import requests
import json
import logging

# RPC URL
RPC_URLS = [
    "https://rpc.ankr.com/eth"
]

# Transaction Hash to inspect
TX_HASH = "0xd4bb10295efa8e76cb6d95a4da5e894dae2fcd529edd203ad9c962d9f206a37b"

def get_tx_receipt(tx_hash):
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
        "id": 1
    }
    
    for rpc in RPC_URLS:
        try:
            print(f"Trying RPC: {rpc}")
            response = requests.post(rpc, json=payload, timeout=5)
            response.raise_for_status()
            data = response.json()
            if data.get('result'):
                return data['result']
        except Exception as e:
            print(f"Error fetching receipt from {rpc}: {e}")
            
    return None

def inspect_tx():
    print(f"Inspecting TX: {TX_HASH}")
    receipt = get_tx_receipt(TX_HASH)
    
    if not receipt:
        print("Receipt not found")
        return

    print(f"Block Number: {int(receipt['blockNumber'], 16)}")
    print(f"Status: {receipt['status']}")
    
    print("\nLogs:")
    for i, log in enumerate(receipt['logs']):
        print(f"\nLog #{i}:")
        print(f"  Address: {log['address']}")
        print(f"  Topics:")
        for t in log['topics']:
            print(f"    - {t}")
        print(f"  Data: {log['data']}")

if __name__ == "__main__":
    inspect_tx()
