import sys
import os
import time
import json
import requests
import psycopg2
import yaml
from datetime import datetime, timezone
from eth_hash.auto import keccak

# Add /opt/airflow/dags and /opt/airflow/include to sys.path
sys.path.append('/opt/airflow')
sys.path.append('/opt/airflow/dags')

# Load DB config or use default Docker connection
DB_URL = "postgresql://airflow:airflow@postgres:5432/chaintelligence"

# DEX CONFIG
DEX_CONFIG_PATH = '/opt/airflow/config/dex_config.yaml'
if not os.path.exists(DEX_CONFIG_PATH):
    # fallback to local relative
    DEX_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'dex_config.yaml')
    
with open(DEX_CONFIG_PATH, 'r') as f:
    DEX_CONFIG = yaml.safe_load(f)

# RPC config
RPC_URLS = {
    "ethereum": os.getenv("RPC_URL_ETHEREUM", "https://eth.llamarpc.com").split(',')[0].strip(),
    "arbitrum": os.getenv("RPC_URL_ARBITRUM", "https://arb1.arbitrum.io/rpc").split(',')[0].strip(),
    "base": os.getenv("RPC_URL_BASE", "https://mainnet.base.org").split(',')[0].strip(),
}

# Subgraph IDs
SUBGRAPHS = {
    "Ethereum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "Arbitrum": "FbCGRftH4a3yZugY7TnbYgPJVEv2LvMT6oF1fxPe9aJM",
    "Base": "43Hwfi3dJSoGpyas9VwNoDAv55yjgGrPpNSmbQZArzMG",
}

def to_checksum_address(address: str) -> str:
    addr_lower = address.lower().replace('0x', '')
    if len(addr_lower) != 40:
        return address
    address_hash = keccak(addr_lower.encode('ascii')).hex()
    return '0x' + ''.join(
        c.upper() if int(address_hash[i], 16) >= 8 else c 
        for i, c in enumerate(addr_lower)
    )

def _derive_address(t0_bytes: bytes, t1_bytes: bytes, fee_val: int, factory_hex: str, init_hash_hex: str, is_v2: bool = False) -> str:
    if is_v2:
        salt = keccak(t0_bytes + t1_bytes)
    else:
        salt = keccak(b'\x00' * 12 + t0_bytes + b'\x00' * 12 + t1_bytes + fee_val.to_bytes(32, 'big'))
    f_bytes = bytes.fromhex(factory_hex.removeprefix('0x'))
    ih_bytes = bytes.fromhex(init_hash_hex.removeprefix('0x'))
    derived = '0x' + keccak(b'\xff' + f_bytes + salt + ih_bytes)[12:].hex()
    return to_checksum_address(derived)

def get_derived_pool_address(network: str, protocol: str, t0_address: str, t1_address: str, fee_tier: str) -> str:
    try:
        proto_key = protocol.lower().replace(' ', '_')
        cfg = DEX_CONFIG.get(proto_key)
        if not cfg:
            return None
        
        is_v2 = "v2" in proto_key
        
        net_cfg = cfg.get(network.lower()) or cfg.get(network)
        if not net_cfg:
            return None
            
        t0_clean = t0_address.lower().strip()
        t1_clean = t1_address.lower().strip()
        t0_bytes, t1_bytes = sorted([bytes.fromhex(t0_clean.removeprefix('0x')), bytes.fromhex(t1_clean.removeprefix('0x'))])
        
        fee_val = 0
        if not is_v2:
            fee_clean = fee_tier.replace('%', '').strip()
            try:
                val = float(fee_clean)
                if val >= 5:
                    fee_val = int(val)
                else:
                    fee_val = int(round(val * 10000))
            except ValueError:
                return None
                    
        return _derive_address(t0_bytes, t1_bytes, fee_val, net_cfg['factory'], net_cfg['init_hash'], is_v2)
    except Exception as e:
        print(f"Error deriving pool address: {e}")
        return None

def fetch_rpc_tvl(network: str, pool_address: str, t0_address: str, t1_address: str, t0_decimals: int, t1_decimals: int, t0_price: float, t1_price: float) -> float:
    rpc_url = RPC_URLS.get(network.lower())
    if not rpc_url:
        return 0.0
        
    SIG_BALANCE_OF = "0x70a08231"
    calldata = SIG_BALANCE_OF + format(int(pool_address, 16), '064x')
    
    payload = [
        {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": t0_address, "data": calldata}, "latest"], "id": 1},
        {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": t1_address, "data": calldata}, "latest"], "id": 2}
    ]
    
    try:
        resp = requests.post(rpc_url, json=payload, timeout=10)
        resp.raise_for_status()
        res_data = resp.json()
        
        bal0_hex = res_data[0].get("result", "0x")
        bal1_hex = res_data[1].get("result", "0x")
        
        bal0 = int(bal0_hex, 16) if bal0_hex != "0x" else 0
        bal1 = int(bal1_hex, 16) if bal1_hex != "0x" else 0
        
        usd0 = (bal0 / (10 ** t0_decimals)) * t0_price
        usd1 = (bal1 / (10 ** t1_decimals)) * t1_price
        
        return usd0 + usd1
    except Exception as e:
        print(f"Error fetching RPC balances for pool {pool_address}: {e}")
        return 0.0

def fetch_dexscreener_tvl(network: str, pool_address: str) -> float:
    net_map = {
        'ethereum': 'ethereum',
        'arbitrum': 'arbitrum',
        'base': 'base',
        'bnb': 'bsc',
        'bsc': 'bsc'
    }
    chain_id = net_map.get(network.lower())
    if not chain_id or not pool_address:
        return 0.0
        
    url = f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{pool_address.lower()}"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            pair = data.get('pair')
            if pair:
                liq_usd = pair.get('liquidity', {}).get('usd')
                if liq_usd is not None:
                    return float(liq_usd)
    except Exception as e:
        print(f"Error fetching DexScreener TVL: {e}")
    return 0.0

def fetch_defillama_tvl(pool_address: str) -> float:
    try:
        resp = requests.get("https://yields.llama.fi/pools", timeout=10)
        if resp.status_code == 200:
            pools = resp.json().get('data', [])
            idx = {}
            for p in pools:
                chain = p.get('chain')
                project = p.get('project')
                symbol = p.get('symbol')
                pool_meta = p.get('poolMeta')
                underlying = p.get('underlyingTokens', [])
                tvl = p.get('tvlUsd')
                uuid = p.get('pool')
                
                net_map = {
                    'Ethereum': 'ethereum', 'Arbitrum': 'arbitrum', 'Base': 'base',
                    'OP Mainnet': 'optimism', 'Polygon': 'polygon', 'BSC': 'bsc',
                    'Avalanche': 'avalanche', 'Celo': 'celo',
                }
                net = net_map.get(chain)
                if not net: continue
                
                proto_map = {
                    'uniswap-v3': ('uniswap_v3', False),
                    'pancakeswap-amm-v3': ('pancakeswap_v3', False),
                    'uniswap-v2': ('uniswap_v2', True),
                }
                proto_info = proto_map.get(project)
                if not proto_info: continue
                protocol, is_v2 = proto_info
                
                if len(underlying) < 2: continue
                t0_addr, t1_addr = underlying[0], underlying[1]
                
                fee_val = 0
                if not is_v2:
                    if not pool_meta or '%' not in pool_meta: continue
                    fee_clean = pool_meta.replace('%', '').strip()
                    try:
                        fee_val = int(round(float(fee_clean) * 10000))
                    except: continue
                    
                derived_addr = get_derived_pool_address(net, protocol, t0_addr, t1_addr, str(fee_clean) + "%" if not is_v2 else "0")
                if derived_addr:
                    idx[derived_addr.lower()] = {
                        'tvl': tvl,
                        'uuid': uuid,
                        'symbol': symbol,
                        'project': project
                    }
            return idx.get(pool_address.lower(), {}).get('tvl', 0.0)
    except Exception as e:
        print(f"Error fetching DeFi Llama TVL: {e}")
    return 0.0

def fetch_subgraph_tvl(network: str, pool_address: str) -> float:
    subgraph_id = SUBGRAPHS.get(network.capitalize())
    if not subgraph_id:
        return 0.0
        
    api_key = os.getenv('GRAPH_API_KEY', 'a09146d9b04d58e07e68bbdca38aa54e')
    url = f"https://gateway-arbitrum.network.thegraph.com/api/{api_key}/subgraphs/id/{subgraph_id}"
    
    query = f"""
    {{
      pool(id: "{pool_address.lower()}") {{
        totalValueLockedUSD
      }}
    }}
    """
    try:
        resp = requests.post(url, json={"query": query}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pool = data.get('data', {}).get('pool')
            if pool:
                return float(pool.get('totalValueLockedUSD', 0) or 0)
    except Exception as e:
        print(f"Error fetching Subgraph TVL: {e}")
    return 0.0

def get_current_prices_llama(network: str, t0: str, t1: str) -> tuple:
    coins = f"{network.lower()}:{t0.lower()},{network.lower()}:{t1.lower()}"
    url = f"https://coins.llama.fi/prices/current/{coins}"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get('coins', {})
            p0 = data.get(f"{network.lower()}:{t0.lower()}", {}).get('price')
            p1 = data.get(f"{network.lower()}:{t1.lower()}", {}).get('price')
            return p0, p1
    except Exception as e:
        print(f"Error fetching llama token prices: {e}")
    return None, None

def run_comparison(pool_id_arg=None):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    query = """
        SELECT lp.id, lp.network, lp.protocol, lp.fee_tier, lp.pool_name,
               c0.symbol, cc0.contract_address, cc0.decimals, c0.price,
               c1.symbol, cc1.contract_address, cc1.decimals, c1.price,
               lp.pool_address
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        LEFT JOIN coin_contract cc0 ON cc0.coin_id = c0.coin_id AND LOWER(cc0.chain) = LOWER(lp.network)
        LEFT JOIN coin_contract cc1 ON cc1.coin_id = c1.coin_id AND LOWER(cc1.chain) = LOWER(lp.network)
        WHERE lp.protocol IN ('Uniswap V3', 'Uniswap V2', 'PancakeSwap V3')
    """
    
    if pool_id_arg:
        query += " AND lp.id = %s"
        cur.execute(query, (pool_id_arg,))
    else:
        query += """ AND lp.network = 'Ethereum' 
                     AND ((c0.symbol IN ('USDC', 'USDT', 'DAI', 'WETH') AND c1.symbol IN ('USDC', 'USDT', 'DAI', 'WETH')))
                     ORDER BY lp.id LIMIT 10"""
        cur.execute(query)
        
    pools = cur.fetchall()
    
    print("\n" + "="*115)
    print(f"{'ID':<4} | {'Pool':<12} | {'Protocol':<12} | {'Address':<42} | {'DB TVL':<10} | {'RPC TVL':<10} | {'DexScr':<10} | {'DefiLlama':<10} | {'Subgraph':<10}")
    print("="*115)
    
    for row in pools:
        pid, network, protocol, fee_tier, name, s0, addr0, dec0, p0, s1, addr1, dec1, p1, db_addr = row
        
        pool_address = db_addr or get_derived_pool_address(network, protocol, addr0, addr1, fee_tier)
        if not pool_address:
            print(f"{pid:<4} | {name:<12} | {protocol:<12} | {'Address not derivable':<42} | {'N/A':<10}")
            continue
            
        cur.execute("SELECT tvl_usd, date FROM liquidity_pool_history WHERE pool_id = %s ORDER BY date DESC LIMIT 1", (pid,))
        db_history_row = cur.fetchone()
        db_tvl = float(db_history_row[0]) if db_history_row else 0.0
        db_date = db_history_row[1] if db_history_row else None
        
        live_p0, live_p1 = get_current_prices_llama(network, addr0, addr1)
        price0 = live_p0 if live_p0 is not None else float(p0 or 1.0)
        price1 = live_p1 if live_p1 is not None else float(p1 or 1.0)
        
        rpc_tvl = fetch_rpc_tvl(network, pool_address, addr0, addr1, dec0, dec1, price0, price1)
        ds_tvl = fetch_dexscreener_tvl(network, pool_address)
        dl_tvl = fetch_defillama_tvl(pool_address)
        sg_tvl = fetch_subgraph_tvl(network, pool_address)
        
        db_tvl_str = f"${db_tvl/1e6:.2f}M" if db_tvl else "N/A"
        rpc_tvl_str = f"${rpc_tvl/1e6:.2f}M" if rpc_tvl else "N/A"
        ds_tvl_str = f"${ds_tvl/1e6:.2f}M" if ds_tvl else "N/A"
        dl_tvl_str = f"${dl_tvl/1e6:.2f}M" if dl_tvl else "N/A"
        sg_tvl_str = f"${sg_tvl/1e6:.2f}M" if sg_tvl else "N/A"
        
        print(f"{pid:<4} | {name:<12} | {protocol:<12} | {pool_address:<42} | {db_tvl_str:<10} | {rpc_tvl_str:<10} | {ds_tvl_str:<10} | {dl_tvl_str:<10} | {sg_tvl_str:<10}")
        if db_date:
            print(f"     | Last DB update date: {db_date}")
            
    print("="*115 + "\n")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    pool_id = None
    if len(sys.argv) > 1:
        pool_id = int(sys.argv[1])
    run_comparison(pool_id)
