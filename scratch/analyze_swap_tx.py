import requests
import json

rpc_urls = [
    "https://ethereum-rpc.publicnode.com",
    "https://rpc.ankr.com/eth",
    "https://eth.llamarpc.com"
]

def rpc_post(payload):
    for u in rpc_urls:
        try:
            r = requests.post(u, json=payload, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if "result" in data:
                    return data["result"]
        except Exception:
            continue
    return None

tx_hash = "0xf769484c9413c2f89fe2cf4eb9302e5e4e6de9b3d33f6509ac2b554efaab1fc2"

payload = {
    "jsonrpc": "2.0",
    "method": "eth_getTransactionReceipt",
    "params": [tx_hash],
    "id": 1
}

receipt = rpc_post(payload)
print("=== Transaction Receipt ===")
if receipt:
    print("Block Number:", int(receipt.get("blockNumber", "0x0"), 16))
    print("To:", receipt.get("to"))
    print("Logs count:", len(receipt.get("logs", [])))
    print("\nLogs:")
    for i, log in enumerate(receipt.get("logs", [])):
        print(f"Log {i} (index {int(log.get('logIndex', '0x0'), 16)}): Address {log.get('address')}")
        print(f"  Topics: {log.get('topics')}")
        print(f"  Data: {log.get('data')[:66]}...")
