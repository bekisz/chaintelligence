import requests
import sys
import json

RPC_URL = "https://rpc.ankr.com/eth/YOUR_RPC_KEY" # Default Eth

def inspect(tx_hash):
    payload = {"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[tx_hash],"id":1}
    try:
        resp = requests.post(RPC_URL, json=payload, timeout=10)
        data = resp.json()
        if 'result' not in data or not data['result']:
            print("No result found.")
            return

        receipt = data['result']
        print(f"Block Number: {int(receipt['blockNumber'], 16)}")
        print(f"Status: {receipt['status']}")
        
        logs = receipt['logs']
        print(f"Logs: {len(logs)}")
        for i, log in enumerate(logs):
            print(f"--- Log {i} ---")
            print(f"Address: {log['address']}")
            print(f"Topics: {log['topics']}")
            print(f"Data: {log['data']}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Default to user provided hash if running without args
        tx = "0xd4bb10295efa8e76cb6d95a4da5e894dae2fcd529edd203ad9c962d9f206a37b"
        inspect(tx)
    else:
        inspect(sys.argv[1])
