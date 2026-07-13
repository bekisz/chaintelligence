import requests

def get_erc20_details_rpc(token_address):
    symbol_payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": token_address, "data": "0x95d89b41"}, "latest"], "id": 1}
    
    rpc_url = "https://cloudflare-eth.com"
    sym_resp = requests.post(rpc_url, json=symbol_payload, timeout=5).json()
    print(f"Full response for {token_address}: {sym_resp}")

get_erc20_details_rpc("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
get_erc20_details_rpc("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2")
