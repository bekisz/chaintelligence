import os
import psycopg2
import re
from dotenv import load_dotenv

load_dotenv('.env.config')
load_dotenv('.env.secrets')

db_url = os.getenv('DATA_WAREHOUSE_DB', 'dbname=chaintelligence user=airflow password=airflow host=localhost port=5433')
db_url = re.sub(r'host=\S+', 'host=localhost', db_url)
db_url = re.sub(r'port=\S+', 'port=5433', db_url)

conn = psycopg2.connect(db_url)
cur = conn.cursor()

print("=== Fetching Sample Pools Across Protocols & Networks ===")
cur.execute("""
    SELECT lp.id, lp.pool_name, lp.pool_address, lp.pool_id, lp.fee_bps,
           pr.name as protocol, ch.name as chain
    FROM liquidity_pool lp
    JOIN protocol pr ON lp.protocol_id = pr.id
    JOIN chain ch ON lp.chain_id = ch.id
    WHERE lp.reverted = false
    LIMIT 20;
""")
pools = cur.fetchall()

def generate_candidate_links(pool_address, v4_pool_id, protocol, network):
    proto_lower = (protocol or "").lower()
    net_lower = (network or "").lower()
    is_v4 = "v4" in proto_lower
    addr = (v4_pool_id if is_v4 and v4_pool_id and len(v4_pool_id) == 66 else pool_address) or ""
    
    links = {}
    if not addr:
        return links
        
    # Map network names for different services
    # 1. Block Explorers (Etherscan, Arbiscan, Basescan, BscScan)
    explorer_base = {
        'ethereum': 'https://etherscan.io/address/',
        'arbitrum': 'https://arbiscan.io/address/',
        'base': 'https://basescan.org/address/',
        'bsc': 'https://bscscan.com/address/',
        'bnb': 'https://bscscan.com/address/',
        'optimism': 'https://optimistic.etherscan.io/address/',
        'polygon': 'https://polygonscan.com/address/'
    }
    net_key = 'ethereum'
    for k in explorer_base:
        if k in net_lower:
            net_key = k
            break
            
    if pool_address and len(pool_address) == 42:
        links['block_explorer'] = explorer_base[net_key] + pool_address
        
    # 2. DexTools
    dextools_chain = {
        'ethereum': 'ether',
        'arbitrum': 'arbitrum',
        'base': 'base',
        'bsc': 'bsc',
        'bnb': 'bsc',
        'optimism': 'optimism',
        'polygon': 'polygon'
    }.get(net_key, 'ether')
    links['dextools'] = f"https://www.dextools.io/app/en/{dextools_chain}/pair-explorer/{addr.lower()}"
    
    # 3. GeckoTerminal
    gecko_chain = {
        'ethereum': 'eth',
        'arbitrum': 'arbitrum',
        'base': 'base',
        'bsc': 'bsc',
        'bnb': 'bsc',
        'optimism': 'optimism',
        'polygon': 'polygon_pos'
    }.get(net_key, 'eth')
    links['geckoterminal'] = f"https://www.geckoterminal.com/{gecko_chain}/pools/{addr.lower()}"
    
    # 4. Revert Finance (for Uniswap V3/V4 LP analytics)
    if 'uniswap' in proto_lower and pool_address:
        revert_chain = {
            'ethereum': 'mainnet',
            'arbitrum': 'arbitrum',
            'base': 'base',
            'optimism': 'optimism',
            'polygon': 'polygon'
        }.get(net_key)
        if revert_chain:
            links['revert_finance'] = f"https://revert.finance/#/sidecar/uniswapv3/{revert_chain}/pool/{pool_address}"

    # 5. Defined.fi
    defined_chain = {
        'ethereum': '1',
        'arbitrum': '42161',
        'base': '8453',
        'bsc': '56',
        'bnb': '56'
    }.get(net_key, '1')
    links['defined_fi'] = f"https://www.defined.fi/{net_key}/{addr.lower()}"
    
    return links

for p in pools:
    pid, name, addr, v4_id, fee, proto, chain = p
    cand = generate_candidate_links(addr, v4_id, proto, chain)
    print(f"\nPool {pid}: {name} ({proto} on {chain})")
    print(f"  Addr: {addr}, V4_ID: {v4_id[:16] if v4_id else 'None'}...")
    for k, v in cand.items():
        print(f"    {k}: {v}")

cur.close()
conn.close()
