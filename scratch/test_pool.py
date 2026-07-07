import requests
import json

url = "https://arb1.arbitrum.io/rpc"
headers = {"Content-Type": "application/json"}

# function token0() external view returns (address)
payload0 = {
    "jsonrpc": "2.0",
    "method": "eth_call",
    "params": [{"to": "0x67D3E181E6dcC47f977c3A4b33Ac65454b87b997", "data": "0x0dfe1681"}, "latest"],
    "id": 1
}
r0 = requests.post(url, headers=headers, json=payload0).json()
print("token0:", r0['result'])

# function token1() external view returns (address)
payload1 = {
    "jsonrpc": "2.0",
    "method": "eth_call",
    "params": [{"to": "0x67D3E181E6dcC47f977c3A4b33Ac65454b87b997", "data": "0xd21220a7"}, "latest"],
    "id": 2
}
r1 = requests.post(url, headers=headers, json=payload1).json()
print("token1:", r1['result'])

# function fee() external view returns (uint24)
payload_fee = {
    "jsonrpc": "2.0",
    "method": "eth_call",
    "params": [{"to": "0x67D3E181E6dcC47f977c3A4b33Ac65454b87b997", "data": "0xddca3f43"}, "latest"],
    "id": 3
}
r_fee = requests.post(url, headers=headers, json=payload_fee).json()
print("fee:", int(r_fee['result'], 16))
