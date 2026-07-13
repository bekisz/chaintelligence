import requests

def test_v4_rpc():
    pm_address = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
    # test token id = 4
    calldata = "0x7ba03aad" + format(4, '064x')
    url = "https://arbitrum.llamarpc.com"
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pm_address, "data": calldata}, "latest"], "id": 1}
    r = requests.post(url, json=payload).json()
    res = r.get("result")
    if res and res != "0x":
        raw = res[2:]
        words = [raw[i:i+64] for i in range(0, len(raw), 64)]
        for i, w in enumerate(words):
            print(f"word {i}: {w}")

if __name__ == "__main__":
    test_v4_rpc()
