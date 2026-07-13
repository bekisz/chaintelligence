import sys
import psycopg2
from eth_hash.auto import keccak

# Mapping of fee to tick spacing for standard hookless pools
_V4_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}

def is_valid_hex_address(addr: str) -> bool:
    if not addr or not isinstance(addr, str):
        return False
    clean = addr.lower().removeprefix('0x')
    if len(clean) != 40:
        return False
    try:
        bytes.fromhex(clean)
        return True
    except ValueError:
        return False

def _derive_v4_pool_id(token0: str, token1: str, fee: int, tick_spacing: int) -> str:
    a = bytes.fromhex(token0.lower().removeprefix('0x'))
    b = bytes.fromhex(token1.lower().removeprefix('0x'))
    if b < a:
        a, b = b, a
    hooks = b'\x00' * 32
    enc = (a.rjust(32, b'\x00') + b.rjust(32, b'\x00') +
           fee.to_bytes(32, 'big') + tick_spacing.to_bytes(32, 'big', signed=True) + hooks)
    return '0x' + keccak(enc).hex()

def main():
    conn = psycopg2.connect('postgresql://airflow:airflow@postgres:5432/chaintelligence')
    cur = conn.cursor()
    
    # 1. Fetch symbol to address map
    cur.execute("""
        SELECT cc.chain, c.symbol, cc.contract_address
        FROM coin_contract cc
        JOIN coin c ON cc.coin_id = c.coin_id
    """)
    symbol_map = {}
    for row in cur.fetchall():
        chain, sym, addr = row
        if sym and addr:
            chain_key = chain.capitalize()
            if chain_key not in symbol_map:
                symbol_map[chain_key] = {}
            symbol_map[chain_key][sym.upper()] = addr.lower()

    # 2. Fetch V4 pools that are missing pool_id
    cur.execute("""
        SELECT lp.id, UPPER(c0.symbol) as s0, UPPER(c1.symbol) as s1,
               lp.fee_tier, lp.network, lp.protocol
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE (lp.protocol = 'Uniswap V4' OR lp.protocol = 'PancakeSwap V4')
          AND lp.pool_id IS NULL
    """)
    missing = cur.fetchall()
    print(f"Found {len(missing)} V4 pools needing derived pool IDs.")
    
    updated = 0
    for row in missing:
        pool_db_id, s0, s1, fee_tier, network, protocol = row
        
        # Resolve token addresses
        chain_key = 'Bsc' if network == 'BNB' else network
        addr0 = symbol_map.get(chain_key, {}).get(s0)
        addr1 = symbol_map.get(chain_key, {}).get(s1)
        
        if not is_valid_hex_address(addr0) or not is_valid_hex_address(addr1):
            continue
            
        # Parse fee to bips
        try:
            if fee_tier.isdigit():
                fee_bips = int(fee_tier)
            else:
                fee_clean = fee_tier.replace('%', '').strip()
                fee_bips = int(round(float(fee_clean) * 10000))
        except:
            continue
            
        # Map fee to tick spacing
        tick_spacing = _V4_TICK_SPACING.get(fee_bips)
        if tick_spacing is None:
            continue
            
        # Derive pool_id
        try:
            pool_id = _derive_v4_pool_id(addr0, addr1, fee_bips, tick_spacing)
        except Exception as e:
            print(f"Error deriving pool_id for pool {pool_db_id}: {e}")
            continue
        
        # Update database
        cur.execute(
            "UPDATE liquidity_pool SET pool_id = %s WHERE id = %s",
            (pool_id, pool_db_id)
        )
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Successfully derived and populated pool_id for {updated} V4 pools.")

if __name__ == '__main__':
    main()
