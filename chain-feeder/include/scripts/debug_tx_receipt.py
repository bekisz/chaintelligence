import requests
import json

RPC = "https://rpc.ankr.com/eth/YOUR_RPC_KEY"
TX_HASH = "0x9c05397b0635d349ec2ddad1d7404ce45eaa7d22d65e86e76369534fc61017e4"

def get_receipt():
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionReceipt",
        "params": [TX_HASH],
        "id": 1
    }
    try:
        resp = requests.post(RPC, json=payload, timeout=10)
        data = resp.json()
        if 'result' in data:
            logs = data['result']['logs']
            print(f"Found {len(logs)} logs in receipt.")
            for i, log in enumerate(logs):
                print(f"--- Log {i} ---")
                print(f"Address: {log['address']}")
                print(f"Topics: {log['topics']}")
                print(f"Data: {log['data']}")
        else:
            print("Receipt not found or empty.")
            print(data)
    except Exception as x:
        print(x)

if __name__ == "__main__":
    get_receipt()
