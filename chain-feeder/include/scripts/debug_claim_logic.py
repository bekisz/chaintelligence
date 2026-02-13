import requests
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RPC = "https://rpc.ankr.com/eth/YOUR_RPC_KEY"
TX_HASH = "0x9c05397b0635d349ec2ddad1d7404ce45eaa7d22d65e86e76369534fc61017e4"

def debug_receipt():
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionReceipt",
        "params": [TX_HASH],
        "id": 1
    }
    try:
        resp = requests.post(RPC, json=payload, timeout=10)
        data = resp.json()
        tx_data = data['result']
        print(f"Block: {int(tx_data['blockNumber'], 16)}")
        logs = tx_data['logs']
        print(f"Receipt has {len(logs)} logs")
        for i, log in enumerate(logs):
            print(f"Log {i}: Address={log['address']}")
            print(f"  Topics: {log['topics']}")
            if len(log['data']) > 2:
                print(f"  Data len: {len(log['data'])}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_receipt()

