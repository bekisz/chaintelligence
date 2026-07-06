import logging
import re
import math
import os
import psycopg2

logger = logging.getLogger(__name__)

# Basic normalization
def normalize_symbol(sym):
    if not sym: return "UNKNOWN"
    s = sym.upper().strip()
    mapping = {'WRAPPED ETHER': 'WETH', 'WRAPPED BITCOIN': 'WBTC'}
    s = mapping.get(s, s)
    return s[:8]

def get_standard_pool_info(label, assets, hardness_map):
    c0, c1 = None, None
    pool_name = None
    reverted = False
    
    if assets and len(assets) >= 2:
        a0 = assets[0]
        a1 = assets[1]
        sym0 = normalize_symbol(a0.get('symbol'))
        sym1 = normalize_symbol(a1.get('symbol'))
        adr0 = a0.get('address', '').lower()
        adr1 = a1.get('address', '').lower()
        
        h0 = hardness_map.get(sym0, 0)
        h1 = hardness_map.get(sym1, 0)
        
        is_swapped = False
        if h0 > h1: is_swapped = True
        elif h0 == h1 and sym0 > sym1: is_swapped = True
        
        if is_swapped:
            c0, c1 = sym1, sym0
            addr_c0, addr_c1 = adr1, adr0
        else:
            c0, c1 = sym0, sym1
            addr_c0, addr_c1 = adr0, adr1
            
        pool_name = f"{c0} - {c1}"
        if addr_c0 and addr_c1 and addr_c0 > addr_c1:
            reverted = True
    return pool_name, c0, c1, reverted

def calculate_v3_amounts(liquidity, tick, tick_lower, tick_upper, d0, d1):
    L = float(liquidity)
    if L == 0: return 0, 0
    
    def get_sqrt_p(t):
        return math.pow(1.0001, t / 2)
    
    sqrt_p = get_sqrt_p(tick)
    sqrt_a = get_sqrt_p(tick_lower)
    sqrt_b = get_sqrt_p(tick_upper)
    
    if tick < tick_lower:
        amount0 = L * (sqrt_b - sqrt_a) / (sqrt_a * sqrt_b)
        amount1 = 0
    elif tick < tick_upper:
        amount0 = L * (sqrt_b - sqrt_p) / (sqrt_p * sqrt_b)
        amount1 = L * (sqrt_p - sqrt_a)
    else:
        amount0 = 0
        amount1 = L * (sqrt_b - sqrt_a)
        
    return amount0 / math.pow(10, d0), amount1 / math.pow(10, d1)

def ingest_coins_data(conn, positions: list):
    if not positions: return
    coins_data = {}
    for p in positions:
        net = p.get('network', 'ethereum').lower()
        for a in p.get('assets', []):
            sym = normalize_symbol(a.get('symbol'))
            addr = a.get('address')
            dec = a.get('decimals', 18)
            if sym and addr:
                key = (sym, net)
                if key not in coins_data:
                    coins_data[key] = {'address': addr, 'decimals': dec}
            
    with conn.cursor() as cur:
        for (sym, net), data in coins_data.items():
            cur.execute("""
                INSERT INTO coin (symbol, decimals, hardness) 
                VALUES (%s, %s, 0) 
                ON CONFLICT (symbol) DO UPDATE SET 
                    decimals = COALESCE(coin.decimals, EXCLUDED.decimals)
                RETURNING coin_id
            """, (sym, data['decimals']))
            coin_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO coin_contract (coin_id, chain, contract_address, decimals, verified_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (coin_id, chain) DO UPDATE SET
                    contract_address = EXCLUDED.contract_address,
                    decimals = COALESCE(EXCLUDED.decimals, coin_contract.decimals),
                    verified_at = EXCLUDED.verified_at
            """, (coin_id, net, data['address'].lower(), data['decimals']))
    conn.commit()

def ingest_pools_data(conn, positions: list):
    if not positions: return
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, hardness FROM coin")
        hardness_map = {row[0].upper(): row[1] for row in cur.fetchall()}
        
        for p in positions:
            pool_name, c0, c1, rev = get_standard_pool_info(p['position_label'], p['assets'], hardness_map)
            if not pool_name: continue
            fee = p.get('fee_tier')
            if fee and str(fee).isdigit() and int(fee) >= 10:
                fee = str(int(fee))
            
            cur.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c0,))
            row0 = cur.fetchone()
            coin0_id = row0[0] if row0 else None
            
            cur.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c1,))
            row1 = cur.fetchone()
            coin1_id = row1[0] if row1 else None
            
            if coin0_id is None or coin1_id is None:
                continue

            cur.execute("""
                INSERT INTO liquidity_pool (network, protocol, pool_name, fee_tier, coin0_id, coin1_id, pool_address, reverted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (network, protocol, pool_name, fee_tier) DO UPDATE
                SET pool_address = COALESCE(liquidity_pool.pool_address, EXCLUDED.pool_address),
                    reverted = EXCLUDED.reverted
            """, (p['network'], p['protocol'], pool_name, fee, coin0_id, coin1_id, p['pool_address'], rev))
    conn.commit()

def ingest_pool_stats(conn, positions: list):
    if not positions: return
    # Get stats from the first position (should be the same for all in this list)
    p = positions[0]
    tvl = p.get('pool_tvl')
    vol = p.get('pool_vol')
    addr = p.get('pool_address')
    
    if tvl is None: 
        return 

    with conn.cursor() as cur:
        # Resolve pool_id
        cur.execute("SELECT id FROM liquidity_pool WHERE pool_address = %s", (addr,))
        res = cur.fetchone()
        if not res: 
            return
        pool_id = res[0]
        
        cur.execute("""
            INSERT INTO liquidity_pool_history (pool_id, date, tvl_usd, volume_usd)
            VALUES (%s, CURRENT_DATE, %s, %s)
            ON CONFLICT (pool_id, date) DO UPDATE 
            SET tvl_usd = EXCLUDED.tvl_usd, 
                volume_usd = EXCLUDED.volume_usd
        """, (pool_id, tvl, vol))
    conn.commit()

def ingest_positions_data(conn, positions: list):
    if not positions: return
    from uniswap_v3_range_fetcher import tick_to_price
    
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, hardness FROM coin")
        hardness_map = {row[0].upper(): row[1] for row in cur.fetchall()}
        
        for p in positions:
            pool_name, _, _, _ = get_standard_pool_info(p['position_label'], p['assets'], hardness_map)
            fee = p.get('fee_tier')
            if fee and str(fee).isdigit() and int(fee) >= 10:
                fee = str(int(fee))
            
            # Priority 1: Match by pool_address (most reliable for V4 and duplicates)
            cur.execute("SELECT id, coin0_symbol, coin1_symbol FROM liquidity_pool WHERE pool_address = %s", (p['pool_address'],))
            res = cur.fetchone()
            
            # Priority 2: Fallback to name/fee matching if address not found
            if not res:
                cur.execute("SELECT id, coin0_symbol, coin1_symbol FROM liquidity_pool WHERE network=%s AND protocol=%s AND pool_name=%s AND fee_tier=%s", 
                           (p['network'], p['protocol'], pool_name, fee))
                res = cur.fetchone()
                
            if not res: continue
            pool_id, c0_sym, c1_sym = res
            
            token_id = None
            match = re.search(r'Token ID:\s*(\d+)', p['position_label'])
            if match: token_id = match.group(1)
            
            p_lower, p_upper = None, None
            if 'tick_lower' in p and 'tick_upper' in p:
                d0, d1 = 18, 18
                for a in p['assets']:
                    if normalize_symbol(a['symbol']) == c0_sym: d0 = a['decimals']
                    if normalize_symbol(a['symbol']) == c1_sym: d1 = a['decimals']
                p_lower = tick_to_price(p['tick_lower'], d0, d1)
                p_upper = tick_to_price(p['tick_upper'], d0, d1)

            cur.execute("""
                INSERT INTO liquidity_pool_position (pool_id, position_key, wallet_address, token_id, tick_lower, tick_upper, price_lower, price_upper)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (position_key) DO UPDATE SET 
                    pool_id = EXCLUDED.pool_id, 
                    token_id = EXCLUDED.token_id,
                    tick_lower = EXCLUDED.tick_lower,
                    tick_upper = EXCLUDED.tick_upper,
                    price_lower = EXCLUDED.price_lower,
                    price_upper = EXCLUDED.price_upper
            """, (pool_id, p['position_key'], p['address'], token_id, p.get('tick_lower'), p.get('tick_upper'), p_lower, p_upper))
    conn.commit()

def ingest_snapshots_data(conn, positions: list):
    if not positions: return
    from uniswap_v3_range_fetcher import tick_to_price
    
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, price FROM coin")
        price_map = {row[0]: float(row[1]) if row[1] else 0.0 for row in cur.fetchall()}
        
        for p in positions:
            cur.execute("SELECT id FROM liquidity_pool_position WHERE position_key = %s", (p['position_key'],))
            res = cur.fetchone()
            if not res: continue
            pos_id = res[0]
            
            v0, v1 = 0, 0
            s0, s1 = None, None
            curr_p, in_range = None, None
            
            if (p['protocol'] == 'Uniswap V3' or p['protocol'] == 'Uniswap V4') and 'liquidity' in p:
                d0, d1 = 18, 18
                if len(p['assets']) >= 2:
                    s0 = normalize_symbol(p['assets'][0]['symbol'])
                    d0 = p['assets'][0]['decimals']
                    s1 = normalize_symbol(p['assets'][1]['symbol'])
                    d1 = p['assets'][1]['decimals']
                
                if 'current_tick' in p and p.get('current_tick') is not None:
                    v0, v1 = calculate_v3_amounts(p['liquidity'], p['current_tick'], p['tick_lower'], p['tick_upper'], d0, d1)
                    curr_p = tick_to_price(p['current_tick'], d0, d1)
                    in_range = p['tick_lower'] <= p['current_tick'] <= p['tick_upper']
            
            balance_usd = (v0 * price_map.get(s0, 0)) + (v1 * price_map.get(s1, 0)) if s0 and s1 else 0

            cur.execute("""
                INSERT INTO liquidity_pool_position_snapshot
                (position_id, timestamp, balance_usd, coin0_amount, coin1_amount, coin0_claimable_amount, coin1_claimable_amount, current_tick, current_price, in_range)
                VALUES (%s, CURRENT_TIMESTAMP, %s, %s, %s, 0, 0, %s, %s, %s)
            """, (pos_id, balance_usd, v0, v1, p.get('current_tick'), curr_p, in_range))
    conn.commit()
