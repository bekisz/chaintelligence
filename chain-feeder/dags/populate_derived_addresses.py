import sys
import os
import psycopg2
import yaml
from eth_hash.auto import keccak

# Add chain-feeder to path
sys.path.append('/opt/airflow')

# DB URL
DB_URL = "postgresql://airflow:airflow@postgres:5432/chaintelligence"

# DEX CONFIG
DEX_CONFIG_PATH = '/opt/airflow/config/dex_config.yaml'
if not os.path.exists(DEX_CONFIG_PATH):
    DEX_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'dex_config.yaml')
    
with open(DEX_CONFIG_PATH, 'r') as f:
    DEX_CONFIG = yaml.safe_load(f)

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

def run_migration():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # Query pools where pool_address is NULL
    cur.execute("""
        SELECT lp.id, lp.network, lp.protocol, lp.fee_tier,
               cc0.contract_address, cc1.contract_address
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        LEFT JOIN coin_contract cc0 ON cc0.coin_id = c0.coin_id AND LOWER(cc0.chain) = LOWER(lp.network)
        LEFT JOIN coin_contract cc1 ON cc1.coin_id = c1.coin_id AND LOWER(cc1.chain) = LOWER(lp.network)
        WHERE lp.pool_address IS NULL
          AND lp.protocol IN ('Uniswap V3', 'Uniswap V2', 'PancakeSwap V3')
    """)
    pools = cur.fetchall()
    print(f"Found {len(pools)} pools needing derived addresses.")
    
    updated_count = 0
    for row in pools:
        pid, network, protocol, fee_tier, t0_addr, t1_addr = row
        if not t0_addr or not t1_addr:
            continue
            
        derived_address = get_derived_pool_address(network, protocol, t0_addr, t1_addr, fee_tier)
        if derived_address:
            cur.execute("UPDATE liquidity_pool SET pool_address = %s WHERE id = %s", (derived_address, pid))
            updated_count += 1
            
    conn.commit()
    cur.close()
    conn.close()
    print(f"Successfully populated pool_address for {updated_count} pools in DB.")

if __name__ == "__main__":
    run_migration()
