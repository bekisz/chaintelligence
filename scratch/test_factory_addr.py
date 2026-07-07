import requests
url = "https://arb1.arbitrum.io/rpc"
headers = {"Content-Type": "application/json"}

# Query creator and transaction of creation if possible, or just call factory() view function
# Uniswap V3 pools have factory() public view returns (address)
payload = {
    "jsonrpc": "2.0",
    "method": "eth_call",
    "params": [{"to": "0x67D3E181E6dcC47f977c3A4b33Ac65454b87b997", "data": "0xc9594683"}, "latest"], # factory() signature hash is 0xc9594683
    "id": 1
}
r = requests.post(url, headers=headers, json=payload).json()
print(r)
