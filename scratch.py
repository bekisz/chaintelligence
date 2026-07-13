import requests

pm_address = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
# Token IDs from DB
tids = [128963, 227109, 299928, 300156, 108827]
rpc_url = "https://rpc.ankr.com/eth/2087a416f7a49024a0de38a87ae2c088cf7aaa743e57d7c9c8c9573aed7829de"

for tid in tids:
    calldata = "0x7ba03aad" + format(tid, '064x')
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pm_address, "data": calldata}, "latest"], "id": 1}
    r = requests.post(rpc_url, json=payload).json()
    res = r.get("result")
    if res and res != "0x":
        raw = res[2:]
        words = [raw[i:i+64] for i in range(0, len(raw), 64)]
        w5 = words[5]
        print(f"Token {tid}: {w5}")
        print(f"  tl_hex: {w5[40:46]} -> int: {int(w5[40:46], 16)}")
        print(f"  tu_hex: {w5[46:52]} -> int: {int(w5[46:52], 16)}")
