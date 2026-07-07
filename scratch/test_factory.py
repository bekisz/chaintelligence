import requests
import json

url = "https://arb1.arbitrum.io/rpc"
headers = {"Content-Type": "application/json"}

# function getPool(address,address,uint24) external view returns (address)
t0 = "0x0000000000000000000000002f2a2543b76a4166549f7aab2e75bef0aefc5b0f"
t1 = "0x000000000000000000000000fd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"
fee = "0000000000000000000000000000000000000000000000000000000000002710" # 10000 in hex is 2710
data = "0x1698ee82" + t0[2:] + t1[2:] + fee

payload = {
    "jsonrpc": "2.0",
    "method": "eth_call",
    "params": [{"to": "0x1F98431c8aD985736e4f3a7465352E461f092301", "data": data}, "latest"],
    "id": 1
}
r = requests.post(url, headers=headers, json=payload).json()
print("getPool:", r.get('result'))
