import requests
url = "https://arb1.arbitrum.io/rpc"
headers = {"Content-Type": "application/json"}

# getCode
payload = {
    "jsonrpc": "2.0",
    "method": "eth_getCode",
    "params": ["0x67D3E181E6dcC47f977c3A4b33Ac65454b87b997", "latest"],
    "id": 1
}
r = requests.post(url, headers=headers, json=payload).json()
code = r.get('result', '')
print("bytecode length:", len(code))
if len(code) > 200:
    print("first 100 chars:", code[:100])
    print("last 100 chars:", code[-100:])
