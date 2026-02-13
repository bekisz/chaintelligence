import requests

RPC = "https://rpc.ankr.com/eth/2087a416f7a49024a0de38a87ae2c088cf7aaa743e57d7c9c8c9573aed7829de"
ADDRESS = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
WALLET = "0xe34eb31bfd2afea4320b1ce0d1b8ae943afac425"

# Scan last 500k blocks (approx 2 months)
# User claims happened in Jan 2026.
# If current is 24M, Jan is 23.9M.
# I'll scan a range.

def get_block():
    r = requests.post(RPC, json={"jsonrpc":"2.0", "method":"eth_blockNumber", "params":[], "id":1})
    return int(r.json()['result'], 16)

def scan():
    curr = get_block()
    start = curr - 500000 
    
    # Topic for address: padded to 32 bytes
    topic_wallet = "0x000000000000000000000000" + WALLET[2:].lower()
    
    print(f"Scanning from {start} to {curr} for topic {WALLET}...")
    
    # Chunk scan
    chunk = 10000
    found = 0
    for s in range(start, curr, chunk):
        e = min(s + chunk, curr)
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getLogs",
            "params": [{
                "fromBlock": hex(s),
                "toBlock": hex(e),
                "address": ADDRESS,
                "topics": [None, topic_wallet] # Try matching as topic 1? Or 2? Or just contain?
                # Usually address is indexed topic 1 or 2.
                # To be sure, I will request generic logs and filter locally if possible, BUT strict generic logs are huge.
                # Better: try specific topic position. Usually recipient is index 1 or 2.
                # Let's try [None, topic_wallet] (Topic 1 matches wallet)
                # AND ALSO [None, None, topic_wallet] (Topic 2 matches wallet)
            }],
            "id": 1
        }
        
        # We need two queries essentially. Or just one OR logic if RPC supports [[A,B]].
        # Standard eth_getLogs supports OR in nested list: [ [T1a, T1b], [T2a] ].
        # But for position-independent search (Topic 1 OR Topic 2 OR Topic 3):
        # We have to issue multiple calls or use [null, topic] then [null, null, topic].
        
        # Try Topic 1 (Transfer(from, TO, ...)) -> To is topic 2 usually.
        # Try Topic 2 (Transfer(FROM, to, ...)) -> From is topic 1.
        # Let's try matching Topic 1, 2, or 3.
        
        # Call 1: Topic 1 = Wallet (e.g. Indexed Param 1)
        p1 = payload.copy()
        p1['params'][0]['topics'] = [None, topic_wallet]
        
        # Call 2: Topic 2 = Wallet (e.g. Indexed Param 2)
        p2 = payload.copy()
        p2['params'][0]['topics'] = [None, None, topic_wallet]

        # Call 3: Topic 3 = Wallet (Indexed Param 3)
        p3 = payload.copy()
        p3['params'][0]['topics'] = [None, None, None, topic_wallet]

        for p in [p1, p2, p3]:
            try:
                resp = requests.post(RPC, json=p, timeout=5)
                data = resp.json()
                if 'result' in data and data['result']:
                    for log in data['result']:
                        print(f"FOUND LOG! Block: {int(log['blockNumber'], 16)}, Tx: {log['transactionHash']}")
                        print(f"Topics: {log['topics']}")
                        print(f"Data: {log['data']}")
                        found += 1
            except Exception as x:
                print(x)
                
    if found == 0:
        print("No logs found matching wallet address.")

if __name__ == "__main__":
    scan()
