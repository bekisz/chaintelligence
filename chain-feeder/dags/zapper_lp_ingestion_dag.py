from airflow import DAG
from airflow.decorators import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Asset
import pendulum
from datetime import timedelta
import logging
import json
import re

# Import existing fetchers
from zapper_client import fetch_zapper_data
from uniswap_v3_range_fetcher import fetch_position_range_data

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
    'CLAIMABLE AAVE ON STKAAVE': 'clAAVE',
    'WRAPPED ETHER': 'WETH',
    'WRAPPED BITCOIN': 'WBTC',
    # Add others if seen frequently
}

def normalize_symbol(sym):
    if not sym: return "UNKNOWN"
    s = sym.upper().strip()
    s = SYMBOL_MAP.get(s, s)
    return s[:8] # Truncate to 8

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
        
    unique_symbols = set()
    
    # Collect symbols from assets and unclaimed
    for p in positions:
        for a in p.get('assets', []):
            unique_symbols.add(normalize_symbol(a.get('symbol')))
        for u in p.get('unclaimed', []):
            unique_symbols.add(normalize_symbol(u.get('symbol')))
            
    # Insert keys if they don't exist
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    inserted = 0
    for sym in unique_symbols:
        # We rely on existing Coin configuration (Hardness/Family) for known coins.
        # For new/unknown coins, we insert with defaults.
        try:
            cur.execute("""
                INSERT INTO coin (symbol, hardness, family)
                VALUES (%s, 0, %s)
                ON CONFLICT (symbol) DO NOTHING
            """, (sym, sym))
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            logging.warning(f"Failed to insert coin {sym}: {e}")
            conn.rollback()
            continue
            
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Ingested {inserted} new coins.")

@task(outlets=[asset_pools])
def ingest_pools(positions: list):
    """Identifies and ingests Liquidity Pools, ensuring correct Coin0/Coin1 ordering."""
    if not positions:
        logging.warning("ingest_pools received empty positions list.")
        return

    logging.info(f"ingest_pools received {len(positions)} positions to process.")

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    # Pre-fetch hardness map
    cur.execute("SELECT symbol, hardness FROM coin")
    hardness_map = {row[0]: row[1] for row in cur.fetchall()}

    for p in positions:
        protocol = p.get('protocol', 'Unknown')
        network = p.get('network', 'Unknown')
        label = p.get('position_label', 'Unknown')
        
        # Clean label to get base Name (remove Token ID)
        pool_name = re.sub(r'(\(Token ID:.*\)|#.*)', '', label).strip()
        
        # Determine Coins from Assets
        # Zapper 'assets' usually contains the 2 LP tokens.
        assets = p.get('assets', [])
        coin_syms = []
        if len(assets) >= 2:
            coin_syms = [normalize_symbol(a.get('symbol')) for a in assets[:2]]
        else:
            # Fallback: Try parsing label? "ETH / USDC"
            # This is risky, but better than nothing if assets missing (unlikely for active pos)
            parts = re.split(r'[\/\-]', pool_name)
            if len(parts) >= 2:
                 coin_syms = [normalize_symbol(parts[0]), normalize_symbol(parts[1])]
            else:
                logging.warning(f"Could not determine tokens for pool {pool_name}. Skipping pool creation.")
                continue

        if len(coin_syms) != 2:
             continue
             
        # Resolve Hardness
        # Default to 0 if unknown
        h0 = hardness_map.get(coin_syms[0], 0)
        h1 = hardness_map.get(coin_syms[1], 0)
        
        # Sort: Coin1 must be HARDER (Higher Score)
        c0, c1 = coin_syms[0], coin_syms[1]
        if h0 > h1:
            c0, c1 = c1, c0 # Swap
        elif h0 == h1:
            if c0 > c1: c0, c1 = c1, c0 # Alphabetic tie-break
            
        # Upsert Pool
        # We don't have Fee Tier from Zapper summary easily unless we fetch Range data.
        # We'll leave fee_tier NULL for now or update it later.
        try:
            cur.execute("""
                INSERT INTO liquidity_pool (network, protocol, pool_name, coin0_symbol, coin1_symbol)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (network, protocol, pool_name, fee_tier) DO NOTHING
            """, (network, protocol, pool_name, c0, c1))
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
    
    for p in positions:
        try:
            label = p.get('position_label', '')
            pool_name = re.sub(r'(\(Token ID:.*\)|#.*)', '', label).strip()
            network = p.get('network')
            protocol = p.get('protocol')
            address = p.get('address')
            
            # Find Pool ID (assuming NULL fee tier for broad match, or need better matching)
            # Problem: If multiple pools exist with same name but diff fee tiers?
            # Zapper name usually doesn't include fee tier "ETH / USDC".
            # We select the first matching pool for now.
            cur.execute("""
                SELECT id FROM liquidity_pool 
                WHERE network = %s AND protocol = %s AND pool_name = %s
                LIMIT 1
            """, (network, protocol, pool_name))
            res = cur.fetchone()
            if not res:
                logging.warning(f"Pool not found for position {label}. Skipping.")
                continue
            pool_id = res[0]
            
            # Helper to extract Token ID locally if possible or leave NULL to be filled by Range Fetcher
            token_id = None
            # Regex extract
            match = re.search(r'Token ID:\s*(\d+)', label, re.IGNORECASE)
            if match: token_id = match.group(1)
            else:
                 m2 = re.search(r'#(\d+)', label)
                 if m2: token_id = m2.group(1)
                 
            # Construct Key
            pos_key = p.get("position_key") or f"{protocol}-{label}-{network}-{address}"
            
            # Upsert Position
            # We do NOT overwrite ranges here. Only Token ID if we found it.
            cur.execute("""
                INSERT INTO liquidity_pool_position (pool_id, position_key, wallet_address, token_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (position_key) DO UPDATE
                SET token_id = COALESCE(liquidity_pool_position.token_id, EXCLUDED.token_id)
            """, (pool_id, pos_key, address, token_id))
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Error ingest position {label}: {e}")

    conn.commit()
    cur.close()
    conn.close()

@task(outlets=[asset_positions])
def fetch_missing_ranges():
    """Fetches range data ONLY for positions that have no range data (Tick Lower is NULL)."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    # Select positions needing update
    # Needs TokenID to fetch.
    cur.execute("""
        SELECT p.id, p.token_id, pool.network, pool.pool_name, p.wallet_address
        FROM liquidity_pool_position p
        JOIN liquidity_pool pool ON p.pool_id = pool.id
        WHERE p.tick_lower IS NULL
          AND p.token_id IS NOT NULL -- Can only fetch if we have Token ID
          AND pool.protocol ILIKE '%Uniswap%'
    """)
    rows = cur.fetchall()
    logging.info(f"Found {len(rows)} positions needing range backfill.")
    
    updated = 0
    for row in rows:
        pos_id, token_id, network, pool_name, wallet = row
        
        # Reconstruct label for fetcher (it expects "Name (Token ID: X)")
        label_for_fetcher = f"{pool_name} (Token ID: {token_id})"
        
        data = fetch_position_range_data(label_for_fetcher, network)
        if data:
            try:
                # Update Position Ranges
                cur.execute("""
                    UPDATE liquidity_pool_position
                    SET tick_lower = %s, tick_upper = %s, price_lower = %s, price_upper = %s
                    WHERE id = %s
                """, (data['tick_lower'], data['tick_upper'], data['price_lower'], data['price_upper'], pos_id))
                
                # Also update Pool Fee Tier if found
                if data.get('fee_tier'):
                    cur.execute("""
                        UPDATE liquidity_pool 
                        SET fee_tier = %s 
                        WHERE id = (SELECT pool_id FROM liquidity_pool_position WHERE id = %s)
                          AND fee_tier IS NULL
                    """, (data['fee_tier'], pos_id))
                
                updated += 1
                conn.commit() # Commit per success to save progress
            except Exception as e:
                conn.rollback()
                logging.error(f"Error updating ranges for {pos_id}: {e}")
        else:
             logging.warning(f"Failed to fetch range for {token_id} on {network}")

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
            
            # Need Pool Coin order to map assets correctly
            cur.execute("SELECT coin0_symbol, coin1_symbol FROM liquidity_pool WHERE id = %s", (pool_id,))
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
            # Assuming rewards are in the constituent tokens
            # Zapper puts pending fees in 'unclaimed'
            r0_amt = 0; r1_amt = 0
            unclaimed = p.get('unclaimed', [])
            for u in unclaimed:
                s = normalize_symbol(u.get('symbol'))
                bal = float(u.get('balance', 0))
                if s == c0_sym: r0_amt = bal
                elif s == c1_sym: r1_amt = bal
                # Note: If reward is a 3rd token (e.g. UNI), we currently ignore it based on schema constraints
                
            # Current Tick/Price/In Range
            # We ONLY have this if we fetch it. 
            # If we didn't run fetcher this time, we insert NULLs.
            # OR we can try to fetch just the pool state here? 
            # User requirement: Fetch only at creation. So we leave NULL.
            
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


with DAG(
    'zapper_lp_ingestion',
    default_args=default_args,
    description='Normalized ingestion of Zapper LP data',
    schedule='*/15 * * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['defi', 'zapper', 'normalized'],
) as dag:

    raw_data = fetch_zapper_balances()
    
    t_coins = ingest_coins(raw_data)
    t_pools = ingest_pools(raw_data)
    t_positions = ingest_positions(raw_data)
    t_ranges = fetch_missing_ranges() # Independent of current batch's content, checks DB
    t_snap = ingest_snapshots(raw_data)
    
    # Dependencies
    raw_data >> t_coins >> t_pools >> t_positions
    t_positions >> t_ranges
    t_positions >> t_snap
