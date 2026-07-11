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
    mapping = {'WRAPPED ETHER': 'WETH', 'WRAPPED BITCOIN': 'WBTC', 'EUROC': 'EURC'}
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
                ON CONFLICT DO NOTHING
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
    from include.uniswap_v3_range_fetcher import tick_to_price
    
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, hardness FROM coin")
        hardness_map = {row[0].upper(): row[1] for row in cur.fetchall()}
        
        for p in positions:
            pool_name, _, _, _ = get_standard_pool_info(p['position_label'], p['assets'], hardness_map)
            fee = p.get('fee_tier')
            if fee and str(fee).isdigit() and int(fee) >= 10:
                fee = str(int(fee))
            
            # Priority 1: Match by pool_address (most reliable for V4 and duplicates)
            cur.execute("""
                SELECT lp.id, c0.symbol, c1.symbol 
                FROM liquidity_pool lp
                JOIN coin c0 ON lp.coin0_id = c0.coin_id
                JOIN coin c1 ON lp.coin1_id = c1.coin_id
                WHERE lp.pool_address = %s
            """, (p['pool_address'],))
            res = cur.fetchone()
            
            # Priority 2: Fallback to name/fee matching if address not found
            if not res:
                cur.execute("""
                    SELECT lp.id, c0.symbol, c1.symbol 
                    FROM liquidity_pool lp
                    JOIN coin c0 ON lp.coin0_id = c0.coin_id
                    JOIN coin c1 ON lp.coin1_id = c1.coin_id
                    WHERE lp.network=%s AND lp.protocol=%s AND lp.pool_name=%s AND lp.fee_tier=%s
                """, (p['network'], p['protocol'], pool_name, fee))
                res = cur.fetchone()
                
            if not res: continue
            pool_id, c0_sym, c1_sym = res
            
            token_id = None
            match = re.search(r'Token ID:\s*(\d+)', p['position_label'])
            if match: token_id = match.group(1)
            
            p_lower, p_upper = None, None
            curr_p = None
            if 'tick_lower' in p and 'tick_upper' in p and len(p['assets']) >= 2:
                d0 = p['assets'][0]['decimals']
                d1 = p['assets'][1]['decimals']
                p_lower = tick_to_price(p['tick_lower'], d0, d1)
                p_upper = tick_to_price(p['tick_upper'], d0, d1)
                if p.get('current_tick') is not None:
                    curr_p = tick_to_price(p['current_tick'], d0, d1)
                
                # Check for price inversion needed (if token0 is stablecoin/quote)
                stablecoins = ["USDC", "USDT", "DAI", "USDBC"]
                quote_currencies = ["WETH", "ETH"]
                t0_sym = p['assets'][0]['symbol'].upper()
                t1_sym = p['assets'][1]['symbol'].upper()
                
                should_invert = False
                if any(s in t0_sym for s in stablecoins) and not any(s in t1_sym for s in stablecoins):
                    should_invert = True
                elif any(q in t0_sym for q in quote_currencies) and \
                     not any(s in t1_sym for s in stablecoins) and \
                     not any(q in t1_sym for q in quote_currencies):
                    should_invert = True
                    
                if should_invert:
                    p_l = p_lower
                    p_u = p_upper
                    p_lower = 1 / p_u if p_u != 0 else 0
                    p_upper = 1 / p_l if p_l != 0 else 0
                    if curr_p:
                        curr_p = 1 / curr_p if curr_p != 0 else 0

            cur.execute("""
                INSERT INTO liquidity_pool_position (pool_id, position_key, wallet_address, token_id, tick_lower, tick_upper, price_lower, price_upper, current_tick, current_price)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (position_key) DO UPDATE SET 
                    pool_id = EXCLUDED.pool_id, 
                    token_id = EXCLUDED.token_id,
                    tick_lower = EXCLUDED.tick_lower,
                    tick_upper = EXCLUDED.tick_upper,
                    price_lower = EXCLUDED.price_lower,
                    price_upper = EXCLUDED.price_upper,
                    current_tick = EXCLUDED.current_tick,
                    current_price = EXCLUDED.current_price
            """, (pool_id, p['position_key'], p['address'], token_id, p.get('tick_lower'), p.get('tick_upper'), p_lower, p_upper, p.get('current_tick'), curr_p))
    conn.commit()

def fetch_unclaimed_fees_rpc(network, token_id):
    if not token_id:
        return 0, 0
        
    NPM_MAP = {
        "Ethereum": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "Arbitrum": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "Base": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "Polygon": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
    }

    # Fetch RPC url
    rpc = None
    if network == "Ethereum":
        rpc = os.getenv("RPC_URL") or os.getenv("RPC_URL_ETHEREUM")
        if rpc and "," in rpc:
            rpc = rpc.split(",")[0].strip()
    
    if not rpc:
        rpc_map = {
            "Ethereum": "https://eth.llamarpc.com",
            "Arbitrum": "https://arb1.arbitrum.io/rpc",
            "Base": "https://mainnet.base.org",
            "Polygon": "https://polygon-rpc.com"
        }
        rpc = rpc_map.get(network)
        
    npm = NPM_MAP.get(network)
    if not npm or not rpc:
        return 0, 0
        
    try:
        import requests
        token_id_hex = hex(int(token_id))[2:].zfill(64)
        recipient_hex = "0".zfill(64)
        amount0_max_hex = "ffffffffffffffffffffffffffffffff".zfill(64)
        amount1_max_hex = "ffffffffffffffffffffffffffffffff".zfill(64)
        
        calldata = "0xfc6f7865" + token_id_hex + recipient_hex + amount0_max_hex + amount1_max_hex
        
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": npm, "data": calldata}, "latest"],
            "id": 1
        }
        
        r = requests.post(rpc, json=payload, timeout=5)
        res = r.json()
        if 'result' in res:
            res_hex = res['result']
            if len(res_hex) >= 130:
                amt0 = int(res_hex[2:66], 16)
                amt1 = int(res_hex[66:130], 16)
                return amt0, amt1
        return 0, 0
    except Exception as e:
        logger.warning(f"Failed to fetch unclaimed fees for token {token_id} on {network}: {e}")
        return 0, 0

def ingest_snapshots_data(conn, positions: list):
    if not positions: return
    from include.uniswap_v3_range_fetcher import tick_to_price
    
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, price FROM coin")
        price_map = {row[0]: float(row[1]) if row[1] else 0.0 for row in cur.fetchall()}
        
        for p in positions:
            cur.execute("""
                SELECT pos.id, c0.symbol, c1.symbol 
                FROM liquidity_pool_position pos
                JOIN liquidity_pool pool ON pos.pool_id = pool.id
                JOIN coin c0 ON pool.coin0_id = c0.coin_id
                JOIN coin c1 ON pool.coin1_id = c1.coin_id
                WHERE pos.position_key = %s
            """, (p['position_key'],))
            res = cur.fetchone()
            if not res: continue
            pos_id, db_c0_sym, db_c1_sym = res
            db_c0_sym = normalize_symbol(db_c0_sym)
            db_c1_sym = normalize_symbol(db_c1_sym)
            
            v0, v1 = 0, 0
            s0, s1 = None, None
            curr_p, in_range = None, None
            d0, d1 = 18, 18
            
            if (p['protocol'] == 'Uniswap V3' or p['protocol'] == 'Uniswap V4') and 'liquidity' in p:
                if len(p['assets']) >= 2:
                    s0 = normalize_symbol(p['assets'][0]['symbol'])
                    d0 = p['assets'][0]['decimals']
                    s1 = normalize_symbol(p['assets'][1]['symbol'])
                    d1 = p['assets'][1]['decimals']
                
                if 'current_tick' in p and p.get('current_tick') is not None:
                    v0, v1 = calculate_v3_amounts(p['liquidity'], p['current_tick'], p['tick_lower'], p['tick_upper'], d0, d1)
                    curr_p = tick_to_price(p['current_tick'], d0, d1)
                    in_range = p['tick_lower'] <= p['current_tick'] <= p['tick_upper']
                    
                    # Apply inversion to snapshot current_price
                    stablecoins = ["USDC", "USDT", "DAI", "USDBC"]
                    quote_currencies = ["WETH", "ETH"]
                    t0_sym = p['assets'][0]['symbol'].upper()
                    t1_sym = p['assets'][1]['symbol'].upper()
                    
                    should_invert = False
                    if any(s in t0_sym for s in stablecoins) and not any(s in t1_sym for s in stablecoins):
                        should_invert = True
                    elif any(q in t0_sym for q in quote_currencies) and \
                         not any(s in t1_sym for s in stablecoins) and \
                         not any(q in t1_sym for q in quote_currencies):
                        should_invert = True
                        
                    if should_invert and curr_p:
                        curr_p = 1 / curr_p if curr_p != 0 else 0
            
            coin0_usd = v0 * price_map.get(s0, 0) if s0 else 0
            coin1_usd = v1 * price_map.get(s1, 0) if s1 else 0
            balance_usd = coin0_usd + coin1_usd

            # Fetch unclaimed fees
            token_id = None
            label = p.get('position_label', '')
            match = re.search(r'Token ID:\s*(\d+)', label)
            if match:
                token_id = match.group(1)
            else:
                pkey = p.get('position_key', '')
                parts = pkey.split('-')
                if len(parts) >= 3 and parts[-1].isdigit():
                    token_id = parts[-1]

            r0_raw, r1_raw = 0, 0
            if token_id and p.get('protocol') == 'Uniswap V3':
                r0_raw, r1_raw = fetch_unclaimed_fees_rpc(p.get('network'), token_id)
                
            r0_amt = float(r0_raw) / (10**d0) if d0 else 0.0
            r1_amt = float(r1_raw) / (10**d1) if d1 else 0.0
            
            r0_usd = r0_amt * price_map.get(s0, 0) if s0 else 0.0
            r1_usd = r1_amt * price_map.get(s1, 0) if s1 else 0.0

            # Align blockchain token order (v0, v1) to database coin order (db_c0_sym, db_c1_sym)
            db_v0, db_v1 = v0, v1
            db_usd0, db_usd1 = coin0_usd, coin1_usd
            if db_c0_sym == s1:
                db_v0, db_v1 = v1, v0
                db_usd0, db_usd1 = coin1_usd, coin0_usd

            db_r0, db_r1 = r0_amt, r1_amt
            db_rusd0, db_rusd1 = r0_usd, r1_usd
            if db_c0_sym == s1:
                db_r0, db_r1 = r1_amt, r0_amt
                db_rusd0, db_rusd1 = r1_usd, r0_usd

            cur.execute("""
                INSERT INTO liquidity_pool_position_snapshot
                (position_id, timestamp, balance_usd, coin0_amount, coin1_amount, 
                 coin0_usd, coin1_usd, coin0_claimable_amount, coin1_claimable_amount, 
                 coin0_claimable_usd, coin1_claimable_usd,
                  current_tick, current_price, in_range)
                VALUES (%s, CURRENT_TIMESTAMP, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (pos_id, balance_usd, db_v0, db_v1, db_usd0, db_usd1, db_r0, db_r1, db_rusd0, db_rusd1, p.get('current_tick'), curr_p, in_range))
    conn.commit()
