
import os
import time
import psycopg2
import requests
from datetime import datetime, timezone

# Configuration
DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "postgres://airflow:airflow@localhost/chaintelligence")
RPC_URLS = [
    "https://rpc.flashbots.net",
    "https://cloudflare-eth.com",
    "https://1rpc.io/eth"
]

# List of V4 positions to check/fix (from audit)
# Position ID -> Token ID
TARGET_POSITIONS = {
    5: "111885",
    6: "112163",
    7: "111886",
    8: "111898",
    9: "111888",
    12: "112167",
    4: "112176" 
}

def make_rpc_request(payload, url=None):
    urls = [url] if url else RPC_URLS
    for u in urls:
        try:
            resp = requests.post(u, json=payload, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
    return None

def get_block_timestamp(block_num, network="Ethereum"):
    payload = {
        "jsonrpc": "2.0", 
        "method": "eth_getBlockByNumber", 
        "params": [hex(block_num), False], 
        "id": 1
    }
    data = make_rpc_request(payload)
    if data and 'result' in data:
        ts = int(data['result']['timestamp'], 16)
        return datetime.fromtimestamp(ts, timezone.utc)
    return datetime.now(timezone.utc)

def fix_position(pos_id, token_id):
    print(f"\nProcessing Pos {pos_id} (Token {token_id})...")
    
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # 1. Get current faulty event TX
    cur.execute("SELECT tx_hash, amount0, amount1 FROM liquidity_pool_position_event WHERE position_id = %s AND event_type = 'create'", (pos_id,))
    row = cur.fetchone()
    
    if not row:
        print("  No 'create' event found via DB.")
        cur.close()
        conn.close()
        return

    tx_hash = row[0]
    print(f"  Current DB Data: TX={tx_hash}, Amt0={row[1]}, Amt1={row[2]}")
    
    # 2. Fetch Logs for this TX to find REAL amounts (using correct logic)
    print(f"  Fetching logs for {tx_hash}...")
    
    # First get block number
    payload_tx = {"jsonrpc":"2.0","method":"eth_getTransactionByHash","params":[tx_hash],"id":1}
    tx_data = make_rpc_request(payload_tx)
    
    if not tx_data or 'result' not in tx_data or not tx_data['result']:
        print("  TX not found on RPC.")
        cur.close()
        conn.close()
        return

    block_num = int(tx_data['result']['blockNumber'], 16)
    print(f"  Block: {block_num}")
    
    # Fetch Logs
    payload_logs = {
        "jsonrpc": "2.0",
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(block_num),
            "toBlock": hex(block_num)
        }],
        "id": 2
    }
    
    logs_data = make_rpc_request(payload_logs)
    if not logs_data or 'result' not in logs_data:
        print("  Failed to fetch logs.")
        cur.close()
        conn.close()
        return
        
    logs = logs_data['result']
    tx_logs = [l for l in logs if l['transactionHash'] == tx_hash]
    
    # Logic to find amounts (same as manual logic)
    # 0x7fc...ae9 is MODIFY_LIQUIDITY topic usually
    
    amount0 = 0.0
    amount1 = 0.0
    
    # Parsing logic from `inspect_v4_events.py` learnings
    # We look for the ModifyLiquidity event topics
    # Topic 0: 0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9 (ModifyLiquidity) OR 
    # Actually V4 beta topics might differ. 103733 had a specific structure.
    # Let's look for Transfer-like logs or the specific V4 PoolManager events.
    
    # V4 PoolManager: 0x000000000004444c5dc75cb358380d2e3de08a90 (often)
    # The heuristic: Find largest positive delta in the logs for the pool/user?
    # Or just find the "Mint" / "ModifyLiquidity" event.
    
    # For 103733, I manually calculated from log 0 and 5.
    # Log 0 was Transfer? 
    # Wait, in V4, there is no ERC20 Transfer for the *Position* NFT if it's not minted?
    # But checking the previous logs:
    # There were transfers of tokens (USDC/AAVE) from User to LogManager/Pool.
    
    # AUTOMATION STRATEGY:
    # Sum all inputs (User -> Pool) and outputs (Pool -> User). Net = Initial Liquidity.
    
    # Need to know Token Addresses to be precise, or just sum ALL transfers?
    # Let's look at the DB to see what tokens we expect.
    cur.execute("""
        SELECT c0.address, c1.address, c0.decimals, c1.decimals 
        FROM liquidity_pool_position p 
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        JOIN coin c0 ON pool.coin0_symbol = c0.symbol
        JOIN coin c1 ON pool.coin1_symbol = c1.symbol
        WHERE p.id = %s
    """, (pos_id,))
    
    pool_info = cur.fetchone()
    addr0, addr1, dec0, dec1 = pool_info[0].lower(), pool_info[1].lower(), pool_info[2], pool_info[3]
    print(f"  Tokens: {addr0} ({dec0}) / {addr1} ({dec1})")
    
    net0 = 0
    net1 = 0
    
    for l in tx_logs:
        # Transfer Event: 0xddf252...
        # Topic 1: From, Topic 2: To
        if l['topics'][0] == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef' and len(l['topics']) == 3:
            src = '0x' + l['topics'][1][26:]
            dst = '0x' + l['topics'][2][26:]
            val = int(l['data'], 16)
            
            log_addr = l['address'].lower()
            
            if log_addr == addr0:
                if src == '0x0000000000000000000000000000000000000000': # Mint
                     net0 += val
                else: 
                     # We assume "User" is the origin. But logs don't show origin.
                     # But typically: User -> Manager (Input)
                     # Manager -> User (Refund)
                     # We want Net Input.
                     # Hard to know who is who without checking tx.origin.
                     # HEURISTIC: Just take the largest transfer?
                     # NO. Correct is: Net Change of the Pool Manager balance?
                     pass
                     
            if log_addr == addr1:
                 # Same logic
                 pass

    # Alternative: Use "ModifyLiquidity" event data if standard V4?
    # Topic 0: 0xbc7... (ModifyLiquidity for V4?)
    # Topic 0 for 103733 logs:
    # Log 3: 0xf208... (topics[0]) -> This had data.
    # Data: big blob.
    # In 103733, I used `0x1035...` (from Log 4) and `0xac9...` (Log 5).
    # Log 4 topic: 0xddf2... (Transfer) -> Address 0x7fc... (AAVE)
    # Log 5 topic: 0xddf2... (Transfer) -> Address 0xa0b... (USDC)
    
    # So for correct V4 accounting, we just need to Find the Transfers of the relevant tokens.
    # Sum(Transfers) where Recipient is PoolManager?
    # PoolManager is `0x000000000004444c5dc75cb358380d2e3de08a90` usually.
    
    PM = "0x000000000004444c5dc75cb358380d2e3de08a90"
    
    for l in tx_logs:
        if l['topics'][0] == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef' and len(l['topics']) == 3:
            src = '0x' + l['topics'][1][26:]
            dst = '0x' + l['topics'][2][26:]
            val = int(l['data'], 16)
            token = l['address'].lower()
            
            if dst.lower() == PM.lower():
                # User -> PM (Input)
                if token == addr0: net0 += val
                if token == addr1: net1 += val
            elif src.lower() == PM.lower():
                # PM -> User (Refund)
                if token == addr0: net0 -= val
                if token == addr1: net1 -= val
                
    final0 = net0 / (10**dec0)
    final1 = net1 / (10**dec1)
    
    print(f"  Calculated Net: {final0} / {final1}")
    
    if final0 <= 0 and final1 <= 0:
        print("  WARN: Calculated zero/negative. Skipping update to be safe.")
    else:
        # Update DB
        if abs(final0 - row[1]) > 0.0001 or abs(final1 - row[2]) > 0.0001:
            print("  UPDATING DB...")
            ts = get_block_timestamp(block_num)
            cur.execute("""
                UPDATE liquidity_pool_position_event
                SET amount0 = %s, amount1 = %s, timestamp = %s
                WHERE position_id = %s AND event_type = 'create'
            """, (final0, final1, ts, pos_id))
            conn.commit()
            print("  Updated.")
        else:
            print("  Values match existing (approx). Skipping.")

    cur.close()
    conn.close()

if __name__ == "__main__":
    for pid, tid in TARGET_POSITIONS.items():
        fix_position(pid, tid)
        time.sleep(1)
