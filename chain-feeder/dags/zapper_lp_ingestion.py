from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Asset
import pendulum
from datetime import timedelta
import logging
import json
import re
import os

# Import existing fetchers
from include.zapper_client import fetch_zapper_data
from include.uniswap_v3_range_fetcher import fetch_position_range_data
from include.cryptocompare_client import fetch_crypto_prices

# Assets
asset_coins = Asset("postgres://postgres:5432/chaintelligence/public/coin")
asset_pools = Asset("postgres://postgres:5432/chaintelligence/public/liquidity_pool")
asset_positions = Asset("postgres://postgres:5432/chaintelligence/public/liquidity_pool_position")
asset_snapshots = Asset("postgres://postgres:5432/chaintelligence/public/liquidity_pool_position_snapshot")

# Configuration
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Symbol Remapping for < 8 chars constraint
SYMBOL_MAP = {
    'SAVINGS USDS': 'sUSDS',
    'CLAIMABLE AAVE ON STKAAVE': 'stkAAVE',
    'WRAPPED ETHER': 'WETH',
    'WRAPPED BITCOIN': 'WBTC',
    # Add others if seen frequently
}

# Symbol mapping for CryptoCompare
PRICE_SYMBOL_MAPPING = {
    'WETH': 'ETH',
    'WBTC': 'BTC',
    'WSTETH': 'ETH',
    'RETH': 'ETH',
    'CBETH': 'ETH',
    'SAVINGS USDS': 'USDS',
    'SUSDS': 'USDS',
}

def normalize_symbol(sym):
    if not sym: return "UNKNOWN"
    s = sym.upper().strip()
    s = SYMBOL_MAP.get(s, s)
    return s[:8] # Truncate to 8

def get_standard_pool_info(label, assets, hardness_map):
    c0, c1 = None, None
    pool_name = None
    reverted = False
    
    # Try from assets
    if assets and len(assets) >= 2:
        a0 = assets[0]
        a1 = assets[1]
        sym0 = normalize_symbol(a0.get('symbol'))
        sym1 = normalize_symbol(a1.get('symbol'))
        adr0 = a0.get('address', '').lower()
        adr1 = a1.get('address', '').lower()
        
        h0 = hardness_map.get(sym0, 0)
        h1 = hardness_map.get(sym1, 0)
        
        # Sort: C0=Softer, C1=Harder
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
        
        # Reverted Logic
        if addr_c0 and addr_c1 and addr_c0 > addr_c1:
            reverted = True
            
    else:
        # Fallback from Label
        base_label = re.sub(r'(\(Token ID:.*\)|#.*)', '', label).strip()
        parts = re.split(r'[\/\-]', base_label)
        if len(parts) >= 2:
            s0 = normalize_symbol(parts[0])
            s1 = normalize_symbol(parts[1])
            
            # Sort: C0=Softer, C1=Harder
            h0 = hardness_map.get(s0, 0)
            h1 = hardness_map.get(s1, 0)
            
            is_swapped = False
            if h0 > h1: is_swapped = True
            elif h0 == h1 and s0 > s1: is_swapped = True
            
            if is_swapped:
                c0, c1 = s1, s0
            else:
                c0, c1 = s0, s1

            pool_name = f"{c0} - {c1}"
            reverted = False # Assumption
        else:
             pool_name = base_label
    
    return pool_name, c0, c1, reverted

@task
def fetch_zapper_balances():
    """Fetches raw LP data from Zapper API."""
    logging.info("Fetching data from Zapper...")
    data = fetch_zapper_data()
    logging.info(f"Fetched {len(data)} positions from Zapper.")
    return data

@task(outlets=[asset_coins])
def ingest_coins(positions: list):
    """Extracts unique tokens from positions and ingests into 'coin' table."""
    if not positions:
        return
        
    # Collect symbols and images
    coin_data = {} # Symbol -> ImageURL
    
    for p in positions:
        assets = p.get('assets', [])
        images = p.get('images', []) # From displayProps
        
        # Heuristic: Map Asset[i] -> Image[i]
        for i, a in enumerate(assets):
            s = normalize_symbol(a.get('symbol'))
            # If we have an image at this index, use it
            img = images[i] if i < len(images) else None
            
            if s and img:
                # Prefer existing non-null, or overwrite if we have nothing
                if s not in coin_data or not coin_data[s]: 
                    coin_data[s] = img
            elif s and s not in coin_data:
                 coin_data[s] = None

        # Unclaimed usually doesn't have separate images in displayProps, 
        # or they might be Mixed. We skip unclaimed image mapping unless we find a better source.
        for u in p.get('unclaimed', []):
            s = normalize_symbol(u.get('symbol'))
            if s not in coin_data:
                coin_data[s] = None
            
    # Insert keys if they don't exist
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    inserted = 0
    updated = 0
    for sym, img in coin_data.items():
        try:
            # Upsert Coin: Insert if new, Update image if missing
            cur.execute("""
                INSERT INTO coin (symbol, hardness, image_url)
                VALUES (%s, 0, %s)
                ON CONFLICT (symbol) DO UPDATE
                SET image_url = COALESCE(coin.image_url, EXCLUDED.image_url)
            """, (sym, img))
            
            if cur.statusmessage.startswith("INSERT"): inserted += 1
            else: updated += 1
            
        except Exception as e:
            logging.warning(f"Failed to upsert coin {sym}: {e}")
            conn.rollback()
            continue
            
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Ingested {inserted} new coins, updated {updated} existing.")

@task(outlets=[asset_coins])
def update_prices():
    """Updates prices in 'coin' table if older than 10 minutes."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    
    # Check if update is needed
    res = pg_hook.get_first("SELECT MAX(price_timestamp) FROM coin")
    last_update = res[0] if res and res[0] else None
    
    now = pendulum.now()
    if last_update and (now - last_update).total_seconds() < 600:
        logging.info(f"Prices are fresh (last update {last_update}). Skipping price refresh.")
        return
    
    logging.info("Prices are stale or missing. Updating all coin prices from CryptoCompare...")
    
    # Get all symbols
    rows = pg_hook.get_records("SELECT symbol FROM coin")
    if not rows:
        return
    
    original_symbols = [row[0] for row in rows]
    fetch_symbols = list(set(PRICE_SYMBOL_MAPPING.get(s.upper(), s.upper()) for s in original_symbols))
    
    all_prices = fetch_crypto_prices(fetch_symbols)
    if not all_prices:
        logging.error("Failed to fetch prices from CryptoCompare.")
        return
    
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    updated = 0
    for sym in original_symbols:
        fetch_sym = PRICE_SYMBOL_MAPPING.get(sym.upper(), sym.upper())
        price = all_prices.get(fetch_sym)
        
        if price is not None:
            try:
                cur.execute("""
                    UPDATE coin 
                    SET price = %s, price_timestamp = %s
                    WHERE symbol = %s
                """, (price, now, sym))
                updated += 1
            except Exception as e:
                logging.error(f"Failed to update price for {sym}: {e}")
                conn.rollback()
    
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Successfully updated {updated} coin prices.")

@task(outlets=[asset_pools])
def ingest_pools(positions: list):
    """Identifies and ingests Liquidity Pools, ensuring correct Coin0/Coin1 ordering based on Hardness."""
    if not positions:
        logging.warning("ingest_pools received empty positions list.")
        return

    logging.info(f"ingest_pools received {len(positions)} positions to process.")

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    # Pre-fetch hardness map
    try:
        cur.execute("SELECT symbol, hardness FROM coin")
        hardness_map = {row[0].upper(): row[1] for row in cur.fetchall()}
    except Exception as e:
        logging.error(f"Error fetching hardness map: {e}")
        hardness_map = {}

    for p in positions:
        protocol = p.get('protocol', 'Unknown')
        network = p.get('network', 'Unknown')
        label = p.get('position_label', 'Unknown')
        assets = p.get('assets', [])
        
        pool_name, c0, c1, reverted = get_standard_pool_info(label, assets, hardness_map)
        
        if not pool_name: continue

        # Upsert Pool
        # Resolve coin0_id and coin1_id from symbol
        cur.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c0,))
        row0 = cur.fetchone()
        coin0_id = row0[0] if row0 else None
        
        cur.execute("SELECT coin_id FROM coin WHERE symbol = %s", (c1,))
        row1 = cur.fetchone()
        coin1_id = row1[0] if row1 else None
        
        if coin0_id is None or coin1_id is None:
            continue

        try:
            cur.execute("""
                INSERT INTO liquidity_pool (network, protocol, pool_name, coin0_id, coin1_id, pool_address, reverted)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (network, protocol, pool_name) DO UPDATE
                SET pool_address = COALESCE(liquidity_pool.pool_address, EXCLUDED.pool_address),
                    reverted = EXCLUDED.reverted,
                    coin0_id = EXCLUDED.coin0_id,
                    coin1_id = EXCLUDED.coin1_id
            """, (network, protocol, pool_name, coin0_id, coin1_id, p.get('pool_address'), reverted))
        except Exception as e:
            conn.rollback()
            logging.error(f"Error inserting pool {pool_name}: {e}")
            
    conn.commit()
    cur.close()
    conn.close()

@task(outlets=[asset_positions])
def ingest_positions(positions: list):
    """Ingests LP Positions linking to Pools."""
    if not positions:
        return

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    # Pre-fetch hardness map
    try:
        cur.execute("SELECT symbol, hardness FROM coin")
        hardness_map = {row[0].upper(): row[1] for row in cur.fetchall()}
    except Exception as e:
        logging.error(f"Error fetching hardness map: {e}")
        hardness_map = {}
    
    for p in positions:
        try:
            label = p.get('position_label', '')
            assets = p.get('assets', [])
            
            pool_name, _, _, _ = get_standard_pool_info(label, assets, hardness_map)
            
            network = p.get('network')
            protocol = p.get('protocol')
            address = p.get('address')
            
            # Find Pool ID (using Standardized Name)
            cur.execute("""
                SELECT id FROM liquidity_pool 
                WHERE network = %s AND protocol = %s AND pool_name = %s
                LIMIT 1
            """, (network, protocol, pool_name))
            res = cur.fetchone()
            if not res:
                logging.warning(f"Pool not found for position {label} (Standard: {pool_name}). Skipping.")
                continue
            pool_id = res[0]
            
            # Helper to extract Token ID
            token_id = None
            match = re.search(r'Token ID:\s*(\d+)', label, re.IGNORECASE)
            if match: token_id = match.group(1)
            else:
                 m2 = re.search(r'#(\d+)', label)
                 if m2: token_id = m2.group(1)
                 
            # Construct Key
            pos_key = p.get("position_key") or f"{protocol}-{label}-{network}-{address}"
            
            # Upsert Position
            cur.execute("""
                INSERT INTO liquidity_pool_position (pool_id, position_key, wallet_address, token_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (position_key) DO UPDATE
                SET token_id = COALESCE(liquidity_pool_position.token_id, EXCLUDED.token_id),
                    pool_id = EXCLUDED.pool_id  -- Update link to New Standard Pool if changed
            """, (pool_id, pos_key, address, token_id))
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Error ingest position {label}: {e}")

    conn.commit()
    cur.close()
    conn.close()

@task(outlets=[asset_positions])
def fetch_missing_ranges():
    """Fetches range data for positions missing ranges OR missing current state."""
    from include.uniswap_v4_range_fetcher import fetch_v4_position_range_data
    from include.uniswap_v4_graph_fetcher import fetch_v4_position_range_data_from_graph
    
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    # Select positions needing update, including protocol
    cur.execute("""
        SELECT p.id, p.token_id, pool.network, pool.pool_name, p.wallet_address, pool.protocol
        FROM liquidity_pool_position p
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        WHERE (p.tick_lower IS NULL OR p.current_tick IS NULL)
          AND p.token_id IS NOT NULL 
          AND pool.protocol ILIKE '%Uniswap%'
    """)
    rows = cur.fetchall()
    logging.info(f"Found {len(rows)} positions needing range/state backfill.")
    
    api_key = os.environ.get("GRAPH_API_KEY")
    
    updated = 0
    for row in rows:
        pos_id, token_id, network, pool_name, wallet, protocol = row
        label_for_fetcher = f"{pool_name} (Token ID: {token_id})"
        
        data = None
        if protocol == 'Uniswap V4':
            # Use Graph-based fetcher for Arbitrum and Base, RPC for Ethereum
            if network in ["Arbitrum", "Base"]:
                data = fetch_v4_position_range_data_from_graph(label_for_fetcher, network, graph_api_key=api_key)
            else:
                data = fetch_v4_position_range_data(label_for_fetcher, network, graph_api_key=api_key)
        else:
            data = fetch_position_range_data(label_for_fetcher, network, graph_api_key=api_key)

        if data:
            try:
                # Update Position Ranges, Current State, AND Fee Tier
                cur.execute("""
                    UPDATE liquidity_pool_position
                    SET tick_lower = %s, tick_upper = %s, 
                        price_lower = %s, price_upper = %s,
                        current_tick = %s, current_price = %s,
                        fee_tier = %s
                    WHERE id = %s
                """, (
                    data['tick_lower'], data['tick_upper'], 
                    data['price_lower'], data['price_upper'], 
                    data['current_tick'], data['current_price'],
                    data.get('fee_tier'),
                    pos_id
                ))
                
                updated += 1
                conn.commit()
            except Exception as e:
                conn.rollback()
                logging.error(f"Error updating ranges for {pos_id}: {e}")
        else:
             logging.warning(f"Failed to fetch range for {token_id} on {network} ({protocol})")

    cur.close()
    conn.close()
    logging.info(f"Backfilled ranges for {updated} positions.")

@task(outlets=[asset_snapshots])
def ingest_snapshots(positions: list):
    """Ingests time-series snapshots."""
    if not positions:
        return
        
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    for p in positions:
        try:
            # Re-derive Key to find ID
            label = p.get('position_label', '')
            network = p.get('network')
            protocol = p.get('protocol')
            address = p.get('address')
            pos_key = p.get("position_key") or f"{protocol}-{label}-{network}-{address}"
            
            cur.execute("SELECT id, pool_id FROM liquidity_pool_position WHERE position_key = %s", (pos_key,))
            res = cur.fetchone()
            if not res: continue
            pos_id, pool_id = res
            
            cur.execute("""
                SELECT c0.symbol, c1.symbol 
                FROM liquidity_pool lp
                JOIN coin c0 ON lp.coin0_id = c0.coin_id
                JOIN coin c1 ON lp.coin1_id = c1.coin_id
                WHERE lp.id = %s
            """, (pool_id,))
            pool_res = cur.fetchone()
            if not pool_res: continue
            c0_sym, c1_sym = pool_res
            
            # Map Assets
            # Initialize
            v0_amt = 0; v1_amt = 0
            
            assets = p.get('assets', [])
            for a in assets:
                s = normalize_symbol(a.get('symbol'))
                bal = float(a.get('balance', 0))
                if s == c0_sym: v0_amt = bal
                elif s == c1_sym: v1_amt = bal
            
            # Map Claimable Rewards (Pending)
            r0_amt = 0; r1_amt = 0
            unclaimed = p.get('unclaimed', [])
            for u in unclaimed:
                s = normalize_symbol(u.get('symbol'))
                bal = float(u.get('balance', 0))
                if s == c0_sym: r0_amt = bal
                elif s == c1_sym: r1_amt = bal
                
            cur.execute("""
                INSERT INTO liquidity_pool_position_snapshot
                (position_id, timestamp, balance_usd, 
                 coin0_amount, coin1_amount, 
                 coin0_claimable_amount, coin1_claimable_amount,
                 coin0_claimed_amount, coin1_claimed_amount)
                VALUES (%s, CURRENT_TIMESTAMP, %s, %s, %s, %s, %s, 0, 0)
            """, (pos_id, p.get('balance_usd', 0), v0_amt, v1_amt, r0_amt, r1_amt))
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Snapshot error for {pos_key}: {e}")
            
    conn.commit()
    cur.close()
    conn.close()


@task
def update_claims_batch(network, protocol):
    """Updates claims for specific network/protocol batch."""
    import sys
    import logging
    # Ensure dags folder is in path for module import if needed
    if "/opt/airflow/dags" not in sys.path:
        sys.path.append("/opt/airflow/dags")
    
    try:
        import backfill_claims_rpc
        # Reload module to ensure latest code if cached (important for long-lived workers)
        import importlib
        importlib.reload(backfill_claims_rpc)
        
        # Override depth to ensure we catch historical claims (default 2M)
        depth = int(os.getenv("CLAIM_SCAN_DEPTH", 2000000))
        backfill_claims_rpc.run_claims_scan(network, protocol, scan_depth_override=depth)
    except ImportError:
        logging.error("Could not import backfill_claims_rpc module.")
        raise


with DAG(
    'zapper_lp_ingestion',
    default_args=default_args,
    description='Normalized ingestion of Zapper LP data',
    schedule='*/15 * * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    max_active_runs=1,
    tags=['defi', 'zapper', 'normalized'],
) as dag:

    raw_data = fetch_zapper_balances()
    
    t_coins = ingest_coins(raw_data)
    t_prices = update_prices()
    t_pools = ingest_pools(raw_data)
    t_positions = ingest_positions(raw_data)
    t_ranges = fetch_missing_ranges() # Independent of current batch's content, checks DB
    t_snap = ingest_snapshots(raw_data)
    
    
    # 7. Update Claims via RPC (Parallel Batches)
    # Define explicitly known batches to enable parallelism
    BATCHES = [
        ("Ethereum", "Uniswap V3"),
        ("Ethereum", "Uniswap V4"),
        ("Arbitrum", "Uniswap V3"),
        ("Base", "Uniswap V3"),
        ("Optimism", "Uniswap V3"), 
        ("Polygon", "Uniswap V3")
    ]
    
    # Filter skipped networks (e.g. SKIP_CLAIM_NETWORKS="Arbitrum,Optimism")
    import os
    skip_nets = [n.strip() for n in os.getenv("SKIP_CLAIM_NETWORKS", "").split(",") if n.strip()]
    
    t_claim_tasks = []
    for net, proto in BATCHES:
        if net in skip_nets:
            continue
            
        # Use task_id that is unique and clean
        tid = f"claims_{net.lower()}_{proto.lower().replace(' ', '_')}"
        t = update_claims_batch.override(task_id=tid)(net, proto)
        t_claim_tasks.append(t)
    
    # Dependencies
    raw_data >> t_coins >> t_prices >> t_pools >> t_positions
    t_positions >> t_ranges
    t_positions >> t_snap >> t_claim_tasks
