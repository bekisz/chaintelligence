
import logging
import requests
import os
import time
import psycopg2
from datetime import datetime, timezone
from collections import defaultdict
from web3 import Web3

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Config
DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "postgres://airflow:airflow@localhost/chaintelligence")

# Addresses
V3_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
# V4 addresses matching backfill_claims_rpc.py
V4_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e" 
V4_CORE_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"

# Topic Hashes
INCREASE_LIQUIDITY_TOPIC = "0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f"
DECREASE_LIQUIDITY_TOPIC = "0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4"
COLLECT_TOPIC = "0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

V4_MODIFY_TOPIC = "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec"

# RPC Lists
RPC_LISTS = {
    "Ethereum": [
        "https://rpc.flashbots.net",
        "https://public.stackup.sh/api/v1/node/ethereum-mainnet",
        "https://rpc.builder0x69.io",
        "https://rpc.mevblocker.io",
        "https://cloudflare-eth.com",
        "https://rpc.ankr.com/eth",
        "https://eth.llamarpc.com",
        "https://1rpc.io/eth"
    ],
    "Arbitrum": ["https://arb1.arbitrum.io/rpc"],
    "Base": ["https://mainnet.base.org"],
}

# Config
BATCH_TOPIC_LIMIT = 20
CHUNK_SIZE = 2000
SCAN_DEPTH = 5000000 # Default scan depth

def make_rpc_request(network, payload):
    rpcs = RPC_LISTS.get(network, [])
    # Add env var RPC if exists
    if network == "Ethereum" and os.environ.get("RPC_URL"):
        rpcs.insert(0, os.environ.get("RPC_URL"))
        
    for rpc in rpcs:
        try:
            resp = requests.post(rpc, json=payload, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(1) # Rate limit, try next
                continue
        except:
            continue
    return None

def get_block_timestamp(network, block_hex):
    payload = {"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":[block_hex, False],"id":2}
    data = make_rpc_request(network, payload)
    if data and 'result' in data:
        return int(data['result']['timestamp'], 16)
    return 0

def parse_v3_log(log, event_type, decimals0, decimals1, token0_is_coin0):
    data_hex = log['data']
    if data_hex.startswith('0x'): data_hex = data_hex[2:]
    
    amount0 = 0
    amount1 = 0
    liquidity = 0
    
    if event_type in ['add_liquidity', 'withdraw']:
        # IncreaseLiquidity / DecreaseLiquidity
        # uint128 liquidity, uint256 amount0, uint256 amount1
        if len(data_hex) >= 192: # 3 * 32 bytes * 2 chars
            liquidity = int(data_hex[0:64], 16)
            amount0 = int(data_hex[64:128], 16)
            amount1 = int(data_hex[128:192], 16)
    
    elif event_type == 'collect_claim':
        # Collect
        # address recipient, uint256 amount0, uint256 amount1
        if len(data_hex) >= 192:
             # recipient = data_hex[0:64]
             amount0 = int(data_hex[64:128], 16)
             amount1 = int(data_hex[128:192], 16)
             
    # Adjust decimals based on token order
    # If token0_is_coin0 is True: amount0 -> coin0 (d0), amount1 -> coin1 (d1)
    # If False (swapped): amount0 -> coin1 (d1), amount1 -> coin0 (d0)
    
    if token0_is_coin0:
        val0 = amount0 / (10**decimals0) if decimals0 else amount0
        val1 = amount1 / (10**decimals1) if decimals1 else amount1
    else:
        # amount0 is for coin1, amount1 is for coin0
        val1 = amount0 / (10**decimals1) if decimals1 else amount0
        val0 = amount1 / (10**decimals0) if decimals0 else amount1
    
    return val0, val1, liquidity

def db_insert_event(cur, pos_id, tx_hash, block, ts, event_type, v0, v1, liquidity=None):
    try:
        cur.execute("""
            INSERT INTO liquidity_pool_position_event 
            (position_id, tx_hash, block_number, timestamp, event_type, amount0, amount1, liquidity_change)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (position_id, tx_hash, event_type) 
            DO UPDATE SET 
                amount0 = EXCLUDED.amount0,
                amount1 = EXCLUDED.amount1,
                liquidity_change = EXCLUDED.liquidity_change,
                timestamp = EXCLUDED.timestamp,
                block_number = EXCLUDED.block_number;
        """, (pos_id, tx_hash, block, ts, event_type, v0, v1, liquidity))
    except Exception as e:
        logger.error(f"DB Error: {e}")

def scan_events(network, protocol, positions):
    is_v4 = "V4" in protocol
    target_address = V3_MANAGER 
    if is_v4:
        logger.warning("V4 Event scanning not fully implemented yet in this script")
        # For V4 we might need different logic
        pass 
        # return # Allow scan if we want to try? V3_MANAGER won't work for V4.

    # Get Current Block
    payload = {"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}
    data = make_rpc_request(network, payload)
    current_block = 0
    if data and 'result' in data:
         current_block = int(data['result'], 16)
    else:
        logger.error(f"Failed to get current block for {network}. Payload: {payload}, Data: {data}")
        return

    min_start = max(0, current_block - SCAN_DEPTH)
    
    # Filter IDs (Token IDs)
    pos_map = {}
    filter_ids = []
    
    for p in positions:
        tid_hex = "0x" + hex(int(p['token_id']))[2:].zfill(64)
        filter_ids.append(tid_hex)
        pos_map[tid_hex.lower()] = p

    if not filter_ids: return

    logger.info(f"Scanning {len(positions)} positions from block {min_start} to {current_block}")
    
    # Log start date
    ts_start = get_block_timestamp(network, hex(min_start))
    date_start = datetime.fromtimestamp(ts_start, timezone.utc).isoformat() if ts_start else "?"
    logger.info(f"Start Date: {date_start}")

    # Batches
    id_batches = [filter_ids[i:i + BATCH_TOPIC_LIMIT] for i in range(0, len(filter_ids), BATCH_TOPIC_LIMIT)]

    # Scan backwards
    total_scan = current_block - min_start
    if total_scan == 0: total_scan = 1 

    # range(start, stop, step) -> backwards means step is -CHUNK_SIZE
    # We want to go from current_block down to min_start
    
    for end in range(current_block, min_start, -CHUNK_SIZE):
        start = max(end - CHUNK_SIZE + 1, min_start)
        
        # Progress logging (inverted logic for backwards)
        scanned_so_far = current_block - end
        if scanned_so_far % (5 * CHUNK_SIZE) < CHUNK_SIZE:
             pct = (scanned_so_far / total_scan) * 100
             logger.info(f"Progress: {start}/{current_block} ({pct:.2f}%)")
             
        unique_logs = {}

        # 1. Scan for Liquidity/Collect Events (Topic 1 = Token ID)
        for id_batch in id_batches:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getLogs",
                "params": [{
                    "fromBlock": hex(start),
                    "toBlock": hex(end),
                    "address": target_address,
                    "topics": [
                        [INCREASE_LIQUIDITY_TOPIC, DECREASE_LIQUIDITY_TOPIC, COLLECT_TOPIC],
                        id_batch
                    ]
                }],
                "id": 1
            }
            data = make_rpc_request(network, payload)
            if data and 'result' in data:
                logs = data['result']
                for l in logs:
                    uid = f"{l['transactionHash']}_{l['logIndex']}"
                    unique_logs[uid] = l

        # 2. Scan for Transfer Events (Topic 3 = Token ID)
        for id_batch in id_batches:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getLogs",
                "params": [{
                    "fromBlock": hex(start),
                    "toBlock": hex(end),
                    "address": target_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        None, # From
                        None, # To
                        id_batch # TokenID match
                    ]
                }],
                "id": 1
            }
            data = make_rpc_request(network, payload)
            if data and 'result' in data:
                logs = data['result']
                for l in logs:
                    uid = f"{l['transactionHash']}_{l['logIndex']}"
                    unique_logs[uid] = l
        
        # Process Logs
        if unique_logs:
            conn = psycopg2.connect(DB_CONN)
            cur = conn.cursor()
            
            # Sort logs
            sorted_logs = sorted(unique_logs.values(), key=lambda x: (int(x['blockNumber'], 16), int(x['logIndex'], 16)))
            
            for log in sorted_logs:
                topic0 = log['topics'][0]
                tx_hash = log['transactionHash']
                block_hex = log['blockNumber']
                block = int(block_hex, 16)
                ts_val = get_block_timestamp(network, block_hex)
                ts = datetime.fromtimestamp(ts_val, timezone.utc)
                
                # Identify Token ID and Position
                token_id_hex = None
                
                if topic0 == TRANSFER_TOPIC:
                    if len(log['topics']) > 3:
                        token_id_hex = log['topics'][3]
                else:
                    if len(log['topics']) > 1:
                        token_id_hex = log['topics'][1]
                        
                if not token_id_hex: continue
                
                p = pos_map.get(token_id_hex.lower())
                if not p: continue
                
                event_type = None
                v0 = 0
                v1 = 0
                liq = 0
                
                if topic0 == INCREASE_LIQUIDITY_TOPIC:
                    event_type = 'add_liquidity'
                    v0, v1, liq = parse_v3_log(log, event_type, p['d0'], p['d1'], p['token0_is_coin0'])
                elif topic0 == DECREASE_LIQUIDITY_TOPIC:
                    event_type = 'withdraw'
                    v0, v1, liq = parse_v3_log(log, event_type, p['d0'], p['d1'], p['token0_is_coin0'])
                elif topic0 == COLLECT_TOPIC:
                    event_type = 'collect_claim'
                    v0, v1, liq = parse_v3_log(log, event_type, p['d0'], p['d1'], p['token0_is_coin0'])
                elif topic0 == TRANSFER_TOPIC:
                    # check from/to
                    try:
                        from_addr = "0x" + log['topics'][1][26:]
                        to_addr = "0x" + log['topics'][2][26:]
                        
                        if int(from_addr, 16) == 0:
                            event_type = 'create'
                            logger.info(f"Position Created: Pos {p['id']} (Token {p['token_id']}) at block {block}")
                        elif int(to_addr, 16) == 0:
                            event_type = 'delete'
                            logger.info(f"Position Deleted: Pos {p['id']} (Token {p['token_id']}) at block {block}")
                    except Exception as e:
                        logger.error(f"Error parsing transfer: {e}")
                    # Ignore other transfers for now
                
                if event_type:
                    logger.info(f"Found {event_type} | Pos {p['id']} | Pool {p['pool_id']} ({p['pool_name']}) | {v0:.4f} {p['c0']} / {v1:.4f} {p['c1']} | Date: {ts} | Block: {block}")
                    db_insert_event(cur, p['id'], tx_hash, block, ts, event_type, v0, v1, liq)
            
            conn.commit()
            cur.close()
            conn.close()

# Uniswap V4 Subgraph URLs
UNISWAP_V4_GRAPHS = {
    "Ethereum": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r",
    "Base": "https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/Gqm2b5J85n1bhCyDMpGbtbVn4935EvvdyHdHrx3dibyj",
}

GRAPH_API_KEY = os.getenv("GRAPH_API_KEY")


def fetch_v4_heuristic(endpoint, pool_id, origin, tick_lower, tick_upper):
    # Query all modifyLiquidities for this user+pool+range
    # This catches "Add Liquidity" events that don't transfer the NFT
    
    query = """
    query GetMods($pool: String!, $origin: String!, $tl: Int!, $tu: Int!) {
      modifyLiquidities(where: { 
        pool: $pool, 
        origin: $origin, 
        tickLower: $tl, 
        tickUpper: $tu 
      }, orderBy: timestamp, orderDirection: asc, first: 1000) {
        id
        timestamp
        amount0
        amount1
        transaction {
          id
          blockNumber
        }
      }
    }
    """
    
    variables = {
        "pool": pool_id,
        "origin": origin,
        "tl": int(tick_lower),
        "tu": int(tick_upper)
    }
    
    try:
        resp = requests.post(endpoint, json={"query": query, "variables": variables}, timeout=10)
        data = resp.json()
        if "data" in data and data["data"]:
            return data["data"].get("modifyLiquidities", [])
    except Exception as e:
        logger.error(f"Heuristic query failed: {e}")
    
    return []

def process_v4_graph_events(transfers, heuristic_mods, position, network):
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    # 1. Process Transfers (Create/Delete) mainly to get IDs and timestamps
    # 2. Process Heuristic Mods (Liquidity Changes)
    
    # We use a dict to merge events by tx_hash
    # event_map[tx_hash] = {type, amt0, amt1, ts, block}
    
    event_map = {}
    
    # Process Transfers first (Create/Delete)
    for t in transfers:
        tx = t['transaction']
        tx_hash = tx['id']
        ts = datetime.fromtimestamp(int(tx['timestamp']), timezone.utc)
        block = int(tx['blockNumber'])
        
        is_create = (t['from'] == "0x0000000000000000000000000000000000000000")
        is_burn = (t['to'] == "0x0000000000000000000000000000000000000000")
        
        if is_create:
            event_map[tx_hash] = {
                "type": "create", "ts": ts, "block": block, "v0": 0.0, "v1": 0.0, "priority": 10
            }
        elif is_burn:
            event_map[tx_hash] = {
                "type": "delete", "ts": ts, "block": block, "v0": 0.0, "v1": 0.0, "priority": 10
            }
            
    # Process Heuristic Mods
    for m in heuristic_mods:
        tx = m['transaction']
        tx_hash = tx['id']
        
        # If we already have a create/delete for this tx, update its amounts
        # If not, it's an "add_liquidity" or "withdraw"
        
        amount0 = float(m['amount0'])
        amount1 = float(m['amount1'])
        
        if tx_hash in event_map:
            # Update existing event (Create/Delete)
            event_map[tx_hash]['v0'] += abs(amount0)
            event_map[tx_hash]['v1'] += abs(amount1)
        else:
            # New event
            ts = datetime.fromtimestamp(int(m['timestamp']), timezone.utc)
            block = int(tx['blockNumber'])
            
            # Determine type
            etype = 'add_liquidity'
            if amount0 < 0 or amount1 < 0:
                etype = 'withdraw' # or collect?
            if amount0 == 0 and amount1 == 0:
                etype = 'collect_claim'
                
            event_map[tx_hash] = {
                "type": etype, "ts": ts, "block": block, 
                "v0": abs(amount0), "v1": abs(amount1),
                "priority": 5
            }
            
    # Insert events
    for tx_hash, data in event_map.items():
        p_v0 = data['v0']
        p_v1 = data['v1']
        
        if not position['token0_is_coin0']:
             p_v0, p_v1 = p_v1, p_v0
             
        logger.info(f"V4 ({network}): Found {data['type']} | {p_v0} / {p_v1} | {data['ts']}")
        db_insert_event(cur, position['id'], tx_hash, data['block'], data['ts'], data['type'], p_v0, p_v1, 0)

    conn.commit()
    cur.close()
    conn.close()


def fetch_v4_events_from_graph(network, position):
    start_time = time.time()
    token_id = position['token_id']
    
    # Correct URL mapping?
    # Ensure we don't use the V3 URL for Ethereum V4
    # We'll use Arbitrum URL for Ethereum as a fallback/hack if the user really meant Arbitrum?
    # Or skip Ethereum to avoid errors?
    # For now, let's keep logic but acknowledge V3 URL fails.
    
    networks_to_try = [network]
    if network == "Ethereum":
        networks_to_try.append("Arbitrum")
        networks_to_try.append("Base")
    
    found_data = False
    
    for net in networks_to_try:
        if net not in UNISWAP_V4_GRAPHS: continue
        
        endpoint = UNISWAP_V4_GRAPHS[net].format(api_key=GRAPH_API_KEY)
        
        # SKIP if using V3 endpoint for V4 (Ethereum)
        if "5zvR" in endpoint and "V4" in position['protocol']: 
             # 5zvR is V3 subgraph.
             continue
             
        logger.info(f"Checking V4 Graph on {net} for Token {token_id}...")
        
        # Step 1: Get Position Details (Transfers + 1 Mod to get Tick/Pool/Origin)
        query = """
        query GetPosition($tokenId: String!) {
          position(id: $tokenId) {
            id
            owner
            transfers(first: 100, orderBy: timestamp, orderDirection: asc) {
              transaction {
                id
                timestamp
                blockNumber
                modifyLiquiditys(first: 1) {
                  pool { id }
                  tickLower
                  tickUpper
                  origin
                }
              }
              from
              to
            }
          }
        }
        """
        
        try:
            resp = requests.post(endpoint, json={"query": query, "variables": {"tokenId": str(token_id)}}, timeout=10)
            data = resp.json()
            
            if "data" in data and data["data"] and data["data"].get("position"):
                pos_data = data["data"]["position"]
                transfers = pos_data.get("transfers", [])
                
                # Check for pool/ticks from the first available modification
                pool_id = None
                origin = None # Use origin from mod, or owner?
                tl = None
                tu = None
                
                # Try to find metadata from any transfer's mod
                for t in transfers:
                    mods = t['transaction'].get('modifyLiquiditys', [])
                    if mods:
                        m = mods[0]
                        pool_id = m['pool']['id']
                        tl = m['tickLower']
                        tu = m['tickUpper']
                        origin = m['origin']
                        break
                
                if pool_id and tl is not None:
                    origins_to_check = set()
                    if origin: origins_to_check.add(origin)
                    if pos_data.get('owner'): origins_to_check.add(pos_data['owner'])
                    # usage of 'wallet_address' from DB if passed in 'position' dict?
                    if position.get('wallet_address'): origins_to_check.add(position['wallet_address'])
                    
                    if not origins_to_check:
                         logger.warning("No origins to check for heuristic")
                    else:
                        logger.info(f"Found Position metadata: Pool {pool_id}, Ticks {tl}/{tu}. Checking origins: {list(origins_to_check)}")
                        
                        all_heuristic_mods = []
                        for org in origins_to_check:
                            partial_mods = fetch_v4_heuristic(endpoint, pool_id, org, tl, tu)
                            all_heuristic_mods.extend(partial_mods)
                            
                        logger.info(f"Heuristic fetched {len(all_heuristic_mods)} modifications total")
                        
                        # Step 3: Process
                        process_v4_graph_events(transfers, all_heuristic_mods, position, net)
                        found_data = True
                        break
                else:
                    logger.warning(f"Position found on {net} but no linking modifyLiquidity metadata available.")
            
        except Exception as e:
            logger.error(f"Graph Req Failed: {e}")

    if not found_data:
        logger.warning(f"No V4 history found for Token {token_id} on any checked network.")


def run_backfill(target_network="Ethereum", target_pos_id=None, target_pool_id=None, scan_all_v4=False):
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    
    sql = """
        SELECT p.id, p.token_id, pool.network, pool.protocol, 
               (SELECT decimals FROM coin WHERE symbol=pool.coin0_symbol LIMIT 1) as d0,
               (SELECT decimals FROM coin WHERE symbol=pool.coin1_symbol LIMIT 1) as d1,
               pool.id, pool.pool_name, pool.coin0_symbol, pool.coin1_symbol,
               (SELECT ethereum_address FROM coin WHERE symbol=pool.coin0_symbol LIMIT 1) as addr0,
               (SELECT ethereum_address FROM coin WHERE symbol=pool.coin1_symbol LIMIT 1) as addr1,
               p.wallet_address
        FROM liquidity_pool_position p
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        WHERE p.token_id IS NOT NULL 
    """
    params = []
    
    if not scan_all_v4:
        sql += " AND pool.network = %s"
        params.append(target_network)
    else:
        # If scanning all V4, we still want to filter somewhat or just grab all V4?
        sql += " AND pool.protocol LIKE '%%V4%%'"

    if target_pos_id:
        sql += " AND p.id = %s"
        params.append(target_pos_id)
        
    if target_pool_id:
        sql += " AND p.pool_id = %s"
        params.append(target_pool_id)

    cur.execute(sql, tuple(params))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    positions = []
    v4_positions = []
    
    for r in rows:
        # Check token sort order
        addr0 = r[10]
        addr1 = r[11]
        token0_is_coin0 = True
        
        if addr0 and addr1:
            if addr0.lower() > addr1.lower():
                token0_is_coin0 = False
        
        pos = {
            "id": r[0], "token_id": r[1], "network": r[2], "protocol": r[3],
            "d0": r[4] or 18, "d1": r[5] or 18,
            "pool_id": r[6], "pool_name": r[7], "c0": r[8], "c1": r[9],
            "token0_is_coin0": token0_is_coin0,
            "wallet_address": r[12]
        }
        
        if "V4" in pos['protocol']:
            v4_positions.append(pos)
        else:
            positions.append(pos)
        
    if positions:
        scan_events(target_network, "Uniswap V3", positions)
        
    if v4_positions:
        logger.info(f"Scanning {len(v4_positions)} V4 positions via Graph...")
        for p in v4_positions:
            # For V4, we ignore the 'network' passed in args if we are in scan_all_v4 mode
            # or we just blindly trust the graph fetcher's fallback logic.
            # We pass p['network'] as the starting point.
            fetch_v4_events_from_graph(p['network'], p)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", default="Ethereum")
    parser.add_argument("--pos_id", type=int, default=None)
    parser.add_argument("--pool_id", type=int, default=None)
    parser.add_argument("--all_v4", action="store_true", help="Scan ALL V4 positions regardless of network label")
    args = parser.parse_args()
    
    run_backfill(args.network, args.pos_id, args.pool_id, args.all_v4)
