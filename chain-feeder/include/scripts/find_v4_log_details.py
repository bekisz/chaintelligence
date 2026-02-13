
import requests
import json

RPC_URL = "https://rpc.flashbots.net"

DATA = [
    # (PosID, TokenID, TxHash)
    (5, "111885", "0xef54cec76bcbf06844f49a09d8feceff2b5275e3a2d73e48770d82a8b782293b"), # Wait, Pos 5 was 0x4c40... in metadata fix?
    (4, "112176", "0xef54cec76bcbf06844f49a09d8feceff2b5275e3a2d73e48770d82a8b782293b")
]
# Let's verify TX hashes from check.
# Fix metadata said:
# PID 5: Found TX 0x4c40bf9e57ad82804c2ef327ffdceb4a088d31d449ce465236deb5b18a0c11a7
# PID 4: Found TX 0xef54cec76bcbf06844f49a09d8feceff2b5275e3a2d73e48770d82a8b782293b

TARGETS = [
    {"id": 5, "token": "111885", "tx": "0x4c40bf9e57ad82804c2ef327ffdceb4a088d31d449ce465236deb5b18a0c11a7"},
    {"id": 4, "token": "112176", "tx": "0xef54cec76bcbf06844f49a09d8feceff2b5275e3a2d73e48770d82a8b782293b"}
]

def analyze_tx(target):
    print(f"\nAnalyzing Pos {target['id']} (Token {target['token']}) in {target['tx']}...")
    tid_hex = hex(int(target['token']))[2:].zfill(64) # no 0x prefix for substring search
    tid_debug = hex(int(target['token']))
    print(f"  Token Hex: {tid_debug}")

    payload = {"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[target['tx']],"id":1}
    data = requests.post(RPC_URL, json=payload).json()
    logs = []

    if 'result' in data and data['result']:
        logs = data['result']['logs']
    else:
        print("  Receipt missing. Falling back to getTransaction + getLogs...")
        # Get Block Number
        tx_payload = {"jsonrpc":"2.0","method":"eth_getTransactionByHash","params":[target['tx']],"id":1}
        tx_resp = requests.post(RPC_URL, json=tx_payload).json()
        if 'result' in tx_resp and tx_resp['result']:
            block_hex = tx_resp['result']['blockNumber']
            print(f"  Block: {int(block_hex, 16)}")
            # Get Logs for full block
            logs_payload = {"jsonrpc":"2.0","method":"eth_getLogs","params":[{"fromBlock": block_hex, "toBlock": block_hex}],"id":1}
            logs_resp = requests.post(RPC_URL, json=logs_payload).json()
            if 'result' in logs_resp:
                # Filter solely for this TX hash
                all_logs = logs_resp['result']
                logs = [l for l in all_logs if l['transactionHash'] == target['tx']]
                print(f"  Found {len(logs)} logs for TX.")
        else:
            print("  TX not found.")
            return

    if not logs:
        print("  No logs found.")
        return
    
    # 1. Find the Transfer Log for this NFT (Mint)
    nft_log_idx = -1
    for i, l in enumerate(logs):
        # Transfer is 0xddf252... 
        # Topic 3 is Token ID (for ERC721)
        # Handle cases where topics length < 4 (not ERC721)
        if len(l['topics']) == 4 and l['topics'][0] == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef':
            # Check topic 3 for token ID
            # topic 3 is 32 bytes hex string
            val_hex = l['topics'][3]
            # remove 0x
            val_int_str = val_hex[2:]
            tid_int_str = hex(int(target['token']))[2:]
            
            # Use strict comparison of suffix or int value
            if int(val_hex, 16) == int(target['token']):
                print(f"  Found NFT Mint Log at Index {i}")
                nft_log_idx = i
                break
    
    if nft_log_idx == -1:
        print("  NFT Mint Log NOT found.")
        # Print all transfer logs just in case
        return

    # 2. Look at surrounding logs for ModifyLiquidity
    # Inspect +/- 5 logs
    start = max(0, nft_log_idx - 5)
    end = min(len(logs), nft_log_idx + 5)
    
    print(f"  Inspecting logs {start} to {end}...")
    for i in range(start, end):
        l = logs[i]
        print(f"  [{i}] Address: {l['address']}")
        print(f"      Topics: {l['topics']}")
        print(f"      Data: {l['data']}")
        
        # Check if Topic 0 matches ModifyLiquidity (0xbc7...) or IncreaseLiquidity (0x7f...)
        # Actually V4 beta signature might correspond to:
        # 0xbc7... is IncreaseLiquidity in V3. 
        # In V4 it is ModifyLiquidity: '0xa0d... '?
        # Let's just look at the DATA length. If it has amounts (int128/uint256), it will be long.

if __name__ == "__main__":
    for t in TARGETS:
        analyze_tx(t)
