import logging
import requests
import json
import os
import sys
import psycopg2
from datetime import datetime

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database Config
DB_CONN = os.getenv("DATA_WAREHOUSE_DB", "postgres://airflow:airflow@postgres/chaintelligence")

# --- Configuration ---

# V3 Graph URLs
UNISWAP_V3_GRAPH_URLS = {
    "Ethereum": "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/3V7ZY6muhxaQL5qvntX1CFXJ32W7BxXZTGTwmpH5J4t3", # Official V3
    "Base": "https://gateway.thegraph.com/api/{api_key}/subgraphs/id/HMuAwufqZ1YCRmzL2SfHTVkzZovC9VL2UAKhjvRqKiR1",
}
GRAPH_API_KEY = os.environ.get("GRAPH_API_KEY")

# V4 Configuration (RPC)
V4_POSITION_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
RPC_URLS = {
    "Ethereum": "https://rpc.ankr.com/eth", # Fallback
}
# V4 Collect Event Signature: Collect(uint256 indexed tokenId, address recipient, uint256 amount0, uint256 amount1)
# Keccak256("Collect(uint256,address,uint256,uint256)")
V4_COLLECT_TOPIC = "0x40d0efd6a53d603e41041cdca74256265cf4593125d0c8340d859424c58f0db3"

# --- Functions ---

def get_db_connection():
    return psycopg2.connect(DB_CONN)

def fetch_v3_claims_graph(token_id, network):
    """Fetch Collect events from The Graph for Uniswap V3."""
    if network not in UNISWAP_V3_GRAPH_URLS:
        logger.warning(f"No V3 Graph URL for {network}")
        return []
    
    url = UNISWAP_V3_GRAPH_URLS[network].format(api_key=GRAPH_API_KEY)
    
    query = """
    query GetClaims($tokenId: String!) {
      position(id: $tokenId) {
        collects(orderBy: timestamp, orderDirection: desc) {
          timestamp
          amount0
          amount1
          transaction {
            id
          }
        }
      }
    }
    """
    
    try:
        resp = requests.post(url, json={'query': query, 'variables': {'tokenId': str(token_id)}})
        if resp.status_code == 200:
            data = resp.json()
            if 'data' in data and data['data'].get('position'):
                return data['data']['position']['collects']
    except Exception as e:
        logger.error(f"V3 Graph Error: {e}")
    
    return []

def fetch_v4_claims_rpc(token_id, network):
    """Fetch Collect events from RPC Logs for Uniswap V4."""
    # Limitation: This effectively scans "recent" history or requires an archive node for full history.
    # For PoC, we scan last 100,000 blocks (~2 weeks on Eth).
    
    rpc_url = os.environ.get("RPC_URL", RPC_URLS.get(network))
    if not rpc_url:
        logger.warning(f"No RPC URL for {network}")
        return []
        
    try:
        # 1. Get current block
        resp = requests.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1})
        current_block = int(resp.json()['result'], 16)
        
        # 2. Define range (last 1M blocks ~ 6 months on Eth L2, ~2 weeks on mainnet? No, Eth 12s/block. 100k = 14 days)
        # We start with 100k for safety.
        from_block = hex(current_block - 100000)
        
        # 3. Filter Logs
        # Topic0: Collect signature
        # Topic1: TokenID (padded to 32 bytes)
        token_id_hex = hex(int(token_id))[2:].zfill(64)
        topic1 = "0x" + token_id_hex
        
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getLogs",
            "params": [{
                "fromBlock": from_block,
                "toBlock": "latest",
                "address": V4_POSITION_MANAGER,
                "topics": [
                    V4_COLLECT_TOPIC,
                    topic1
                ]
            }],
            "id": 2
        }
        
        logger.info(f"Scanning V4 logs from block {int(from_block,16)}...")
        resp = requests.post(rpc_url, json=payload)
        logs = resp.json().get('result', [])
        
        claims = []
        for log in logs:
            # Parse data: recipient (32 bytes), amount0 (32 bytes), amount1 (32 bytes)
            # data is non-indexed params.
            # topic1 is tokenId.
            # Event: Collect(tokenId, recipient, amount0, amount1)
            # Indexed: tokenId.
            # Non-indexed: recipient, amount0, amount1.
            
            data = log['data'][2:] # remove 0x
            # recipient = data[0:64]
            amount0_hex = data[64:128]
            amount1_hex = data[128:192]
            
            amount0 = int(amount0_hex, 16)
            amount1 = int(amount1_hex, 16)
            
            # We need timestamp. Must fetch block.
            block_hex = log['blockNumber']
            # Optimization: create block_timestamp cache if many logs
            b_resp = requests.post(rpc_url, json={"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":[block_hex, False],"id":3})
            ts = int(b_resp.json()['result']['timestamp'], 16)
            
            claims.append({
                "timestamp": ts,
                "amount0": str(amount0), # String to match Graph format
                "amount1": str(amount1),
                "transaction": {"id": log['transactionHash']}
            })
            
        return claims

    except Exception as e:
        logger.error(f"V4 RPC Error: {e}")
        return []

def main():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get all active positions
    cur.execute("""
        SELECT p.id, p.token_id, ch.name AS network, pr.name AS protocol, pool.pool_name, p.wallet_address
        FROM liquidity_pool_position p
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        JOIN chain ch ON pool.chain_id = ch.id
        JOIN protocol pr ON pool.protocol_id = pr.id
        WHERE p.token_id IS NOT NULL
        ORDER BY p.id
    """)
    positions = cur.fetchall()
    
    print(f"Found {len(positions)} positions. Checking for claims...")
    
    for row in positions:
        pos_id, token_id, network, protocol, pool_name, address = row
        print(f"\nPosition {pos_id}: {pool_name} (Token {token_id}) [{protocol}]")
        
        claims = []
        if "V3" in protocol:
            claims = fetch_v3_claims_graph(token_id, network)
        elif "V4" in protocol:
            claims = fetch_v4_claims_rpc(token_id, network)
        
        if claims:
            print(f"  > Found {len(claims)} claim events!")
            for c in claims:
                dt = datetime.fromtimestamp(int(c['timestamp']))
                print(f"    - {dt}: {c['amount0']} / {c['amount1']} (Tx: {c['transaction']['id'][:10]}...)")
        else:
            print("  > No claims found (in recent history/query).")
            
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
