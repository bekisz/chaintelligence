
import requests
import json

TX_HASH = "0xef54cec76bcbf06844f49a09d8feceff2b5275e3a2d73e48770d82a8b782293b"
RPC_URL = "https://rpc.flashbots.net"

def debug():
    print(f"Debugging TX: {TX_HASH}")
    
    # 1. Get Receipt
    payload = {"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[TX_HASH],"id":1}
    resp = requests.post(RPC_URL, json=payload)
    data = resp.json()
    
    if 'result' in data and data['result']:
        print("Receipt Found!")
        logs = data['result']['logs']
        for i, l in enumerate(logs):
            print(f"Log {i}: Address {l['address']}")
            print(f"  Topics: {l['topics']}")
            print(f"  Data: {l['data']}")
    else:
        print("Receipt NOT Found. Trying getTransaction...")
        payload_tx = {"jsonrpc":"2.0","method":"eth_getTransactionByHash","params":[TX_HASH],"id":1}
        resp = requests.post(RPC_URL, json=payload_tx)
        tx_data = resp.json()
        
        if 'result' in tx_data and tx_data['result']:
            block = tx_data['result']['blockNumber']
            print(f"TX in Block: {int(block, 16)}")
            
            # Fetch Block Logs
            payload_logs = {
                "jsonrpc": "2.0",
                "method": "eth_getLogs",
                "params": [{"fromBlock": block, "toBlock": block}],
                "id": 1
            }
            resp = requests.post(RPC_URL, json=payload_logs)
            logs_data = resp.json()
            if 'result' in logs_data:
                logs = [l for l in logs_data['result'] if l['transactionHash'] == TX_HASH]
                print(f"Found {len(logs)} logs for TX in block.")
                for i, l in enumerate(logs):
                    print(f"Log {i}: Address {l['address']}")
                    print(f"  Topics: {l['topics']}")
                    print(f"  Data: {l['data']}")
            else:
                print("Failed to fetch block logs.")

if __name__ == "__main__":
    debug()
