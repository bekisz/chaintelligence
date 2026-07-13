import requests

def test_v4_rpc():
    pm_address = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
    # Find a valid token ID by looking at potential_tids in logs or DB
    # We saw "token_id": 4 previously? Let's use 1000 or a large number just to see.
    # Actually, we can fetch all V4 tokens for one of the user's addresses.
    url = "https://gateway-arbitrum.network.thegraph.com/api/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"
    q = """{ modifyLiquidities(first: 5) { tokenId } }"""
    r = requests.post(url, json={"query": q}).json()
    token_ids = [m["tokenId"] for m in r.get("data", {}).get("modifyLiquidities", [])]
    
    if not token_ids:
        print("No tokens found")
        return
        
    tid = int(token_ids[0])
    print(f"Testing with Token ID: {tid}")
    
    calldata = "0x7ba03aad" + format(tid, '064x')
    rpc_url = "https://arbitrum.llamarpc.com"
    payload = {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pm_address, "data": calldata}, "latest"], "id": 1}
    r = requests.post(rpc_url, json=payload).json()
    res = r.get("result")
    if res and res != "0x":
        raw = res[2:]
        words = [raw[i:i+64] for i in range(0, len(raw), 64)]
        print(f"Total words: {len(words)}")
        for i, w in enumerate(words):
            print(f"word {i}: {w}")
    else:
        print("Empty or error response:", r)

if __name__ == "__main__":
    test_v4_rpc()
