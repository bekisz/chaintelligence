import requests

def get_storage(addr, pos):
    url = "https://arb1.arbitrum.io/rpc"
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": addr, "data": pos}, "latest"],
        "id": 1
    }
    r = requests.post(url, json=payload)
    print(r.json())

# token0() signature is 0x0dfe1681
get_storage("0x5777a83d47f9f257b4202242137910086a872168", "0x0dfe1681")
# token1() signature is 0xd21220a7
get_storage("0x5777a83d47f9f257b4202242137910086a872168", "0xd21220a7")
# fee() signature is 0xddca3f43
get_storage("0x5777a83d47f9f257b4202242137910086a872168", "0xddca3f43")

