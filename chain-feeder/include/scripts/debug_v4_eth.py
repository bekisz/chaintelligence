import requests
import json

RPC_URL = "https://eth.llamarpc.com" # Ethereum Mainnet
POSITION_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
SEL_GET_INFO = "0x7ba03aad"
TOKEN_ID = 111885

def call_rpc(to, data):
    payload = {"jsonrpc":"2.0", "method":"eth_call", "params":[{"to":to, "data":data}, "latest"], "id":1}
    resp = requests.post(RPC_URL, json=payload)
    return resp.json().get('result')

def debug_v4_eth():
    calldata = SEL_GET_INFO + format(TOKEN_ID, '064x')
    print(f"Calling {POSITION_MANAGER} on Ethereum with {calldata}")
    res = call_rpc(POSITION_MANAGER, calldata)
    print(f"Result: {res}")
    
    if res and res != "0x":
        raw = res[2:]
        words = [raw[i:i+64] for i in range(0, len(raw), 64)]
        for idx, w in enumerate(words):
            val = int(w, 16)
            print(f"Word {idx}: {w} -> Int: {val}")

if __name__ == "__main__":
    debug_v4_eth()
