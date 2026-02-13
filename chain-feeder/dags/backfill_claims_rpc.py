import logging
import requests
import os
import time
import psycopg2
from datetime import datetime, timezone
from collections import defaultdict

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Config
DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "postgres://airflow:airflow@postgres/chaintelligence")

# Addresses
V3_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
V4_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
V4_CORE_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"

# Topics
# Uniswap V3 Collect Event (on NonfungiblePositionManager)
# Collect(uint256 indexed tokenId, address recipient, uint256 amount0, uint256 amount1)
COLLECT_TOPIC = "0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01"
V4_MODIFY_TOPIC = "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# RPCs
RPC_URLS = {
    "Ethereum": os.getenv("RPC_URL", "https://eth.llamarpc.com"),
    "Arbitrum": "https://arb1.arbitrum.io/rpc",
    "Base": "https://mainnet.base.org",
}

# Scan Constants
CHUNK_SIZE = 500 # Reduced from 2000 to prevent timeouts/zombies
SCAN_DEPTH = int(os.getenv("CLAIM_SCAN_DEPTH", 2000000)) 
BATCH_TOPIC_LIMIT = 20 # Reduced from 50

def get_rpc(network):
    if network == "Ethereum" and os.environ.get("RPC_URL"):
        return os.environ.get("RPC_URL")
    return RPC_URLS.get(network)

def get_block_timestamp(rpc_url, block_hex):
    try:
        payload = {"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":[block_hex, False],"id":2}
        resp = requests.post(rpc_url, json=payload, timeout=5)
        return int(resp.json()['result']['timestamp'], 16)
    except:
        return 0

def fetch_tx_receipt_transfers(rpc_url, tx_hash, from_address, min_log_index):
    try:
        payload = {"jsonrpc":"2.0","method":"eth_getTransactionReceipt","params":[tx_hash],"id":1}
        resp = requests.post(rpc_url, json=payload, timeout=5)
        data = resp.json()
        if 'result' not in data or data['result'] is None: return []
        
        logs = data['result']['logs']
        transfers = []
        logs.sort(key=lambda x: int(x['logIndex'], 16))
        
        for log in logs:
            idx = int(log['logIndex'], 16)
            if idx <= min_log_index: continue
            
            # Stop if new ModifyLiquidity event from Core
            if len(log['topics']) > 0 and log['topics'][0] == V4_MODIFY_TOPIC:
                if log['address'].lower() == V4_CORE_MANAGER.lower(): break
            
            if len(log['topics']) > 0 and log['topics'][0] == TRANSFER_TOPIC:
                if len(log['topics']) > 1:
                    sender = "0x" + log['topics'][1][26:] 
                    if sender.lower() == from_address.lower():
                        amt = int(log['data'], 16)
                        transfers.append({"token": log['address'], "amount": amt})
        return transfers
    except Exception as e:
        logger.error(f"Receipt fetch error: {e}")
        return []

def scan_batch(rpc_url, network, protocol, positions, scan_depth_override=None):
    """
    Batched scanning for a group of positions on same network/protocol.
    """
    is_v4 = "V4" in protocol
    target_address = V4_CORE_MANAGER if is_v4 else V3_MANAGER
    depth = scan_depth_override if scan_depth_override else SCAN_DEPTH
    
    # 1. Determine Scan Range
    current_block = 0
    for _ in range(3):
        try:
            r = requests.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}, timeout=10)
            if r.status_code==200 and r.json().get('result'):
                current_block = int(r.json()['result'], 16)
                break
        except: time.sleep(1)
            
    if current_block == 0:
        logger.error(f"Could not get block number for {network}")
        return {}, 0

    # Find min start block
    min_start = current_block
    for p in positions:
        last = p['last_scan']
        start = (last + 1) if last and last > 0 else (current_block - depth)
        if start < 0: start = 0
        if start < min_start: min_start = start
    
    if min_start > current_block:
        return {}, 0
        
    # Get Dates for logging
    ts_start = get_block_timestamp(rpc_url, hex(min_start))
    ts_end = get_block_timestamp(rpc_url, hex(current_block))
    date_start = datetime.fromtimestamp(ts_start, timezone.utc).isoformat() if ts_start else "?"
    date_end = datetime.fromtimestamp(ts_end, timezone.utc).isoformat() if ts_end else "?"
        
    logger.info(f"Scanning {len(positions)} pos ({protocol}) from {min_start} ({date_start}) to {current_block} ({date_end})")
    
    # 2. Build Filter Topics (Batched)
    # V3: Topic1 = [TokenID1, TokenID2, ...]
    # V4: Topic1 = [PoolID1, PoolID2, ...]
    
    filter_ids = []
    pos_map = defaultdict(list) # specific_id -> [pos objects]
    
    valid_positions = []
    
    for p in positions:
        if is_v4:
            # V4 needs Pool Address (ID)
            pid = p['pool_addr']
            if pid and len(pid) == 66:
                filter_ids.append(pid)
                pos_map[pid.lower()].append(p)
                valid_positions.append(p)
        else:
            # V3 needs Token ID
            tid_hex = "0x" + hex(int(p['token_id']))[2:].zfill(64)
            filter_ids.append(tid_hex)
            pos_map[tid_hex.lower()].append(p)
            valid_positions.append(p)
    
    if not filter_ids:
        logger.warning("No valid IDs for filtering batch.")
        return {}, 0

    # Chunk filter_ids (Global BATCH_TOPIC_LIMIT used)
    # Note: filter_ids are passed to topics, casing matters? 
    # Usually RPC accepts mixed case but returns specific case.
    # Topic filters are usually case-sensitive bytes32? 
    # Actually bytes32 hex strings.
    # I'll assume filter_ids are fine as is (from DB), but lookup map uses lower.
    
    id_batches = [filter_ids[i:i + BATCH_TOPIC_LIMIT] for i in range(0, len(filter_ids), BATCH_TOPIC_LIMIT)]
    
    claims_by_pos = defaultdict(list)

    # Scan Loop
    for start in range(min_start, current_block, CHUNK_SIZE):
        time.sleep(0.05) # Yield CPU to prevent zombie kills
        
        end = min(start + CHUNK_SIZE - 1, current_block)
        
        if (start - min_start) % (10 * CHUNK_SIZE) == 0:
             prog = int((start - min_start) / (current_block - min_start) * 100)
             logger.info(f"Batch Progress: {prog}% ({start}/{current_block})")
        
        # We might need multiple RPC calls per chunk if we have many ID batches
        unique_logs = {} # dedup logs by tx_log_index
        
        for id_batch in id_batches:
            topic0 = V4_MODIFY_TOPIC if is_v4 else COLLECT_TOPIC
            topic1 = id_batch # List of IDs -> OR logic
            topic2 = ("0x" + "0"*24 + V4_MANAGER[2:].lower()) if is_v4 else None
            
            topics = [topic0, topic1]
            if topic2: topics.append(topic2)
            
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getLogs",
                "params": [{"fromBlock": hex(start), "toBlock": hex(end), "address": target_address, "topics": topics}],
                "id": 1
            }
            
            # Retry
            logs = None
            for _ in range(3):
                try:
                    resp = requests.post(rpc_url, json=payload, timeout=15)
                    data = resp.json()
                    res = data.get('result')
                    if res is not None:
                        logs = res
                        break
                    time.sleep(2)
                except: time.sleep(1)
            
            if logs:
                for l in logs:
                    uid = f"{l['transactionHash']}_{l['logIndex']}"
                    unique_logs[uid] = l

        # Process logs
        for log in unique_logs.values():
            ts = get_block_timestamp(rpc_url, log['blockNumber'])
            if ts == 0: continue
            
            if not is_v4:
                # V3 Parsing
                tid_hex = log['topics'][1] # Indexed Topic 1
                positions_for_log = pos_map.get(tid_hex.lower(), [])
                
                data_hex = log['data'][2:]
                if len(data_hex) < 192: continue
                amt0 = int(data_hex[64:128], 16)
                amt1 = int(data_hex[128:192], 16)
                
                for p in positions_for_log:
                    # Check if this log is within p's scan range
                    blk = int(log['blockNumber'], 16)
                    target_start = (p['last_scan'] + 1) if p['last_scan'] else 0
                    if blk >= target_start:
                        claims_by_pos[p['id']].append({
                            "ts": datetime.fromtimestamp(ts, timezone.utc),
                            "val0": amt0 / (10**p['d0']),
                            "val1": amt1 / (10**p['d1']),
                            "block": blk
                        })
            else:
                # V4 Parsing
                # Topic 1 is Pool ID
                pid_hex = log['topics'][1]
                positions_for_log = pos_map.get(pid_hex.lower(), [])
                
                data_hex = log['data'][2:]
                if len(data_hex) < 256: continue
                
                salt_hex = data_hex[192:256]
                if not salt_hex: continue
                salt_id = int(salt_hex, 16)
                
                for p in positions_for_log:
                    # Match Token ID
                    if str(p['token_id']) == str(salt_id):
                        blk = int(log['blockNumber'], 16)
                        target_start = (p['last_scan'] + 1) if p['last_scan'] else 0
                        if blk >= target_start:
                            # Fetch Receipt logic
                            log_index = int(log['logIndex'], 16)
                            tx_transfers = fetch_tx_receipt_transfers(rpc_url, log['transactionHash'], V4_CORE_MANAGER, log_index)
                            
                            c0 = p['c0_addr']; c1 = p['c1_addr']
                            a0 = 0; a1 = 0
                            if c0 and c1:
                                for t in tx_transfers:
                                    if t['token'].lower() == c0.lower(): a0 += t['amount']
                                    elif t['token'].lower() == c1.lower(): a1 += t['amount']
                            
                            if a0 > 0 or a1 > 0:
                                claims_by_pos[p['id']].append({
                                    "ts": datetime.fromtimestamp(ts, timezone.utc),
                                    "val0": a0 / (10**p['d0']),
                                    "val1": a1 / (10**p['d1']),
                                    "block": blk
                                })
    
    # Return results including current block for timestamp update
    return claims_by_pos, current_block

def run_claims_scan(target_network=None, target_protocol=None, scan_depth_override=None):
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # Use override or global const
    depth = scan_depth_override if scan_depth_override else SCAN_DEPTH
    
    logger.info("Fetching relevant positions...")
    
    query = """
        SELECT p.id, p.token_id, pool.network, pool.protocol, pool.pool_name, p.last_claim_scan_block, 
               (SELECT ethereum_address FROM coin WHERE symbol=pool.coin0_symbol LIMIT 1) as c0_addr,
               (SELECT ethereum_address FROM coin WHERE symbol=pool.coin1_symbol LIMIT 1) as c1_addr,
               pool.pool_address,
               (SELECT decimals FROM coin WHERE symbol=pool.coin0_symbol LIMIT 1) as d0,
               (SELECT decimals FROM coin WHERE symbol=pool.coin1_symbol LIMIT 1) as d1
        FROM liquidity_pool_position p
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        WHERE p.token_id IS NOT NULL
    """
    params = []
    
    if target_network:
        query += " AND pool.network = %s"
        params.append(target_network)
    if target_protocol:
        query += " AND pool.protocol = %s"
        params.append(target_protocol)
        
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    
    # Group positions
    groups = defaultdict(list)
    for row in rows:
        pid, tid, net, proto, name, last, c0, c1, paddr, d0, d1 = row
        obj = {
            "id": pid, "token_id": tid, "last_scan": last, "pool_addr": paddr,
            "c0_addr": c0, "c1_addr": c1, "d0": (d0 if d0 else 18), "d1": (d1 if d1 else 18),
            "name": name
        }
        groups[(net, proto)].append(obj)
        
    logger.info(f"Grouped {len(rows)} positions into {len(groups)} batches.")
    
    for (net, proto), positions in groups.items():
        logger.info(f"Processing Batch: {net} {proto} ({len(positions)} positions)")
        rpc = get_rpc(net)
        if not rpc: continue
        
        claims_map, end_blk = scan_batch(rpc, net, proto, positions, depth)
        if not claims_map and end_blk == 0:
            logger.warning(f"Batch {net} {proto} returned no results/progress.")
            continue
            
        # Apply Updates
        total_claims = 0
        for pid, claims in claims_map.items():
            total_claims += len(claims)
            for c in claims:
                 cur.execute("""
                    UPDATE liquidity_pool_position_snapshot
                    SET coin0_claimed_amount = COALESCE(coin0_claimed_amount, 0) + %s,
                        coin1_claimed_amount = COALESCE(coin1_claimed_amount, 0) + %s
                    WHERE position_id = %s AND timestamp >= %s
                """, (c['val0'], c['val1'], pid, c['ts']))
        
        conn.commit()
        logger.info(f" -> Applied {total_claims} claims.")
        
        if end_blk > 0:
            for p in positions:
                cur.execute("UPDATE liquidity_pool_position SET last_claim_scan_block = %s WHERE id = %s", (end_blk, p['id']))
            conn.commit()
            
    cur.close()
    conn.close()
    logger.info("Batch scan complete.")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", help="Filter by network")
    parser.add_argument("--protocol", help="Filter by protocol")
    args = parser.parse_args()
    
    run_claims_scan(args.network, args.protocol)

if __name__ == "__main__":
    main()
