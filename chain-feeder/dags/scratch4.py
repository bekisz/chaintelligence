import requests

def test_v4_rpc():
    pm_address = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
    # test token id = 4
    calldata = "0x7ba03aad" + format(103718, '064x') # Let's use a known token ID! But wait, I don't know a valid V4 token ID.
    url = "https://rpc.ankr.com/eth"
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pm_address, "data": calldata}, "latest"], "id": 1}
    r = requests.post(url, json=payload).json()
    print(r)

if __name__ == "__main__":
    test_v4_rpc()
