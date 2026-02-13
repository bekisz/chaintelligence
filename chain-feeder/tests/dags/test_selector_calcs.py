import requests
import json
import binascii

RPC_URL = "https://rpc.ankr.com/eth/YOUR_RPC_KEY"

SIGS = [
    "getPoolAndPositionInfo(uint256)",
    "positions(uint256)",
    "pools(bytes32)",
    "ownerOf(uint256)"
]

def to_hex(s):
    return "0x" + binascii.hexlify(s.encode('utf-8')).decode('utf-8')

def check():
    for sig in SIGS:
        hex_data = to_hex(sig)
        payload = {"jsonrpc": "2.0", "method": "web3_sha3", "params": [hex_data], "id": 1}
        try:
            resp = requests.post(RPC_URL, json=payload, timeout=5)
            res = resp.json()
            if "result" in res:
                sel = res["result"][:10] # 0x + 8 chars
                print(f"{sig} -> {sel}")
            else:
                print(f"{sig} -> Error: {res}")
        except Exception as e:
            print(f"{sig} -> Exception: {e}")

if __name__ == "__main__":
    check()
