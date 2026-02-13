import requests
import json
import logging

RPC_URL = "https://rpc.ankr.com/eth/YOUR_RPC_KEY"

CANDIDATES = [
    ("Candidate V4", "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"),
    ("Uniswap V3", "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"),
]

SEL_OWNER_OF = "0x6352211e" # ownerOf(uint256)
TID = 124668

def call_rpc(to, data):
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": to, "data": data}, "latest"], "id": 1}
    try:
        resp = requests.post(RPC_URL, json=payload, timeout=5)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def check():
    data = SEL_OWNER_OF + format(TID, '064x')
    
    for name, addr in CANDIDATES:
        print(f"Checking {name} ({addr})...")
        res = call_rpc(addr, data)
        
        if "result" in res:
            # Success
            owner = "0x" + res["result"][-40:]
            print(f"  SUCCESS! Owner: {owner}")
        else:
            print(f"  FAILED: {res.get('error')}")

if __name__ == "__main__":
    check()
