from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
from datetime import datetime, timedelta, timezone
import logging

# Configuration
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

HARDNESS_MAP = {
    'USDC': 1000, 'USDT': 990, 'DAI': 970, 'GHO': 950,
    'WBTC': 870, 'WETH': 860, 'ETH': 860,
    'LINK': 850, 'UNI': 840, 'AAVE': 820
}

def get_base_asset_order(sym0, sym1):
    h0 = HARDNESS_MAP.get(sym0, 0)
    h1 = HARDNESS_MAP.get(sym1, 0)
    
    # Sort: C0=Softer, C1=Harder
    is_swapped = False
    if h0 > h1: is_swapped = True
    elif h0 == h1 and sym0 > sym1: is_swapped = True
    
    if is_swapped:
        return sym1, sym0
    else:
        return sym0, sym1

def normalize_fee_tier(fee_str):
    """
    Normalizes fee string (e.g. '0.05%') to bips string (e.g. '500')
    """
    if not fee_str: return None
    # If already bips (integer-like), return as string
    # We check if it is purely digits
    if fee_str.isdigit(): return fee_str
    
    # Map percentages
    mapping = {
        '0.01%': '100',
        '0.05%': '500',
        '0.08%': '800',
        '0.3%': '3000',
        '1.0%': '10000'
    }
    return mapping.get(fee_str.strip(), fee_str)

@task
def sync_pools_from_swaps():
    """
    Scans the unified swaps table for Uniswap V4 token pairings and ensures
    they exist in the liquidity_pool table.
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    # Distinct pairings from the unified swaps table (coin_id FKs → symbols).
    logging.info("Scanning swaps table for new Uniswap V4 pools...")
    cur.execute("""
        SELECT DISTINCT s.network, c0.symbol, c1.symbol, s.fee_display
        FROM swaps s
        JOIN coin c0 ON s.t0_coin_id = c0.coin_id
        JOIN coin c1 ON s.t1_coin_id = c1.coin_id
        WHERE s.protocol = 'Uniswap V4'
    """)
    rows = cur.fetchall()

    new_pools = 0
    for network, s0, s1, fee in rows:
        if not s0 or not s1:
            continue
        c0, c1 = get_base_asset_order(s0.upper(), s1.upper())
        pool_name = f"{c0} - {c1}"
        fee_bips = normalize_fee_tier(fee)

        cur.execute("SELECT coin_id FROM coin WHERE UPPER(symbol) = %s", (c0,))
        row0 = cur.fetchone()
        cur.execute("SELECT coin_id FROM coin WHERE UPPER(symbol) = %s", (c1,))
        row1 = cur.fetchone()
        if not row0 or not row1:
            continue

        try:
            cur.execute("""
                INSERT INTO liquidity_pool (network, protocol, pool_name, coin0_id, coin1_id, fee_tier)
                VALUES (%s, 'Uniswap V4', %s, %s, %s, %s)
                ON CONFLICT (network, protocol, pool_name, fee_tier) DO NOTHING
            """, (network, pool_name, row0[0], row1[0], fee_bips))
            if cur.statusmessage.startswith("INSERT 0 1"):
                new_pools += 1
        except Exception as e:
            conn.rollback()
            logging.warning(f"Failed to sync pool {pool_name}: {e}")

    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Synced {new_pools} new Uniswap V4 pools from swaps.")

@task
def build_daily_history():
    """
    Aggregates daily volume and tx counts from uniswap_v4_swaps
    and upserts into liquidity_pool_history.
    """
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    logging.info("Refreshing liquidity_pool_history from swaps...")
    
    # Complex query to:
    # 1. Aggregate swaps by Day + TokenPair + Fee
    # 2. Join with liquidity_pool to get proper pool_id
    # 3. Upsert into history
    
    # We use CASE statement to normalize swap fees (0.05% -> 500) to match pool table
    
    query = """
    INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd)
    SELECT
        p.id AS pool_id,
        DATE(s.ts) AS date,
        COUNT(*) AS tx_count,
        SUM(s.amount_usd) AS volume_usd
    FROM swaps s
    JOIN liquidity_pool p ON
        p.network = s.network AND p.protocol = 'Uniswap V4' AND
        p.fee_tier = CASE s.fee_display
            WHEN '0.01%' THEN '100'
            WHEN '0.05%' THEN '500'
            WHEN '0.08%' THEN '800'
            WHEN '0.25%' THEN '2500'
            WHEN '0.3%'  THEN '3000'
            WHEN '1.0%'  THEN '10000'
            ELSE s.fee_display
        END AND
        (
            (p.coin0_id = s.t0_coin_id AND p.coin1_id = s.t1_coin_id) OR
            (p.coin0_id = s.t1_coin_id AND p.coin1_id = s.t0_coin_id)
        )
    WHERE s.protocol = 'Uniswap V4' AND s.amount_usd IS NOT NULL
    GROUP BY p.id, DATE(s.ts)
    ON CONFLICT (pool_id, date) DO UPDATE
    SET tx_count = EXCLUDED.tx_count,
        volume_usd = EXCLUDED.volume_usd;
    """
    
    cur.execute(query)
    updated_rows = cur.rowcount
    conn.commit()
    
    cur.close()
    conn.close()
    logging.info(f"Updated {updated_rows} daily history records.")

@task
def sync_v4_pool_ids():
    """
    Queries the V4 subgraph for each liquidity_pool row that lacks a pool_id
    and UPDATEs it with the poolId (bytes32 hex).

    The V4 subgraph pool entity's 'id' IS the poolId
    (= keccak256(abi.encode(PoolKey))).
    """
    from common.utils.uniswap_utils import UniswapV4Fetcher

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    # 1. Build symbol→address map by network from coin_contract (official addresses).
    # (The unified swaps table no longer stores token addresses, so the old
    # swap-frequency heuristic is gone; coin_contract covers the tracked tokens
    # that V4 routing cares about.)
    from collections import defaultdict
    symbol_map = defaultdict(dict)

    cur.execute("""
        SELECT cc.chain, c.symbol, cc.contract_address
        FROM coin_contract cc
        JOIN coin c ON cc.coin_id = c.coin_id
    """)
    for row in cur.fetchall():
        chain, sym, addr = row
        if sym and addr:
            chain_key = chain.capitalize()
            symbol_map[chain_key][sym.upper()] = addr.lower()

    # 2. Get V4 pools that are missing pool_id
    cur.execute("""
        SELECT lp.id, UPPER(c0.symbol) as s0, UPPER(c1.symbol) as s1,
               lp.fee_tier, lp.network
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE lp.protocol = 'Uniswap V4'
          AND lp.pool_id IS NULL
    """)
    missing = cur.fetchall()
    logging.info(f"Found {len(missing)} V4 pools without pool_id")

    if not missing:
        logging.info("No V4 pools need pool_id — nothing to do.")
        cur.close()
        conn.close()
        return

    # 3. Query subgraph per pool to get poolId
    updated = 0
    for pool_row in missing:
        pool_db_id, c0, c1, fee, network = pool_row
        if not fee:
            continue

        net_symbol_map = symbol_map.get(network, {})
        addr0 = net_symbol_map.get(c0.upper())
        addr1 = net_symbol_map.get(c1.upper())

        if not addr0 or not addr1:
            logging.warning(
                f"Cannot resolve addresses for pool {pool_db_id} "
                f"({c0}-{c1}) on {network}"
            )
            continue

        # Normalize fee to bips for subgraph query
        try:
            if fee == 'Dynamic':
                fee_bips = 8388608
            elif fee.isdigit():
                fee_bips = int(fee)
            else:
                fee_clean = fee.replace('%', '').strip()
                fee_bips = int(round(float(fee_clean) * 10000))
        except:
            continue

        # Query subgraph for this pool
        fetcher = UniswapV4Fetcher(verbose=False, network=network)
        try:
            t0, t1 = sorted([addr0.lower(), addr1.lower()])
            query = f"""
            {{
              pools(where: {{
                token0: "{t0}",
                token1: "{t1}",
                feeTier: "{fee_bips}"
              }}) {{
                id
              }}
            }}
            """
            result = fetcher._execute_query(query)
        except Exception as e:
            logging.warning(
                f"Query failed for pool {pool_db_id} ({c0}-{c1}) "
                f"on {network}: {e}"
            )
            continue

        if not result or 'data' not in result:
            continue

        pools = result['data'].get('pools', [])
        if not pools:
            continue

        # If multiple pools match (different hooks/spacing), take the first
        pool_id = pools[0].get('id')
        if not pool_id:
            continue

        cur.execute(
            "UPDATE liquidity_pool SET pool_id = %s WHERE id = %s",
            (pool_id, pool_db_id)
        )
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Updated {updated} V4 pool rows with pool_id.")


@task
def sync_tvl_from_graph():
    """
    Fetches daily TVL/Volume/TxCount from The Graph for all active pools
    and upserts into liquidity_pool_history.
    """
    from common.utils.uniswap_utils import UniswapV4Fetcher
    
    fetcher = UniswapV4Fetcher(verbose=True)
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()
    
    # 1. Build Symbol -> Address Map by Network from coin_contract.
    # (Unified swaps no longer stores token addresses, so the old swap-frequency
    # heuristic is gone; coin_contract covers the tracked V4 tokens.)
    from collections import defaultdict
    logging.info("Building symbol->address map by network...")
    symbol_map = defaultdict(dict)
    cur.execute("""
        SELECT cc.chain, c.symbol, cc.contract_address
        FROM coin_contract cc
        JOIN coin c ON cc.coin_id = c.coin_id
    """)
    for row in cur.fetchall():
        chain, sym, addr = row
        if sym and addr:
            symbol_map[chain.capitalize()][sym.upper()] = addr.lower()
                
    # 2. Get all pools
    cur.execute("""
        SELECT lp.id, c0.symbol, c1.symbol, lp.fee_tier, lp.network 
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE lp.protocol = 'Uniswap V4'
    """)
    pools = cur.fetchall()
    
    # Keep network-specific fetcher instances
    fetchers = {
        "Ethereum": UniswapV4Fetcher(verbose=True, network="Ethereum"),
        "Arbitrum": UniswapV4Fetcher(verbose=True, network="Arbitrum"),
        "Base": UniswapV4Fetcher(verbose=True, network="Base"),
        "BNB": UniswapV4Fetcher(verbose=True, network="BNB")
    }
    
    for pool in pools:
        pool_id, c0, c1, fee, network = pool
        if not fee: continue
        
        net_symbol_map = symbol_map[network]
        addr0 = net_symbol_map.get(c0.upper())
        addr1 = net_symbol_map.get(c1.upper())
        
        if not addr0 or not addr1:
            logging.warning(f"Skipping pool {pool_id} ({c0}-{c1}) on {network}: Address not found for symbols.")
            continue
            
        try:
            if fee == 'Dynamic':
                fee_bips = 8388608
            elif fee.isdigit():
                fee_bips = int(fee)
            else:
                fee_clean = fee.replace('%', '').strip()
                fee_bips = int(round(float(fee_clean) * 10000))
        except:
            continue
           
        # Fetch last 90 days
        start_date = datetime.now(timezone.utc) - timedelta(days=90)
        
        fetcher = fetchers.get(network, fetchers["Ethereum"])
        try:
            data = fetcher.fetch_pool_daily_data(addr0, addr1, fee_bips, start_date)
        except Exception as e:
            logging.error(f"Error fetching Graph data for pool {c0}-{c1} on {network}: {e}")
            continue
        
        if not data:
            continue
            
        logging.info(f"Upserting {len(data)} records for pool {c0}-{c1} (TVL/Vol)")
        for d in data:
            cur.execute("""
                INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd, tvl_usd)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (pool_id, date) DO UPDATE 
                SET tvl_usd = COALESCE(liquidity_pool_history.tvl_usd, EXCLUDED.tvl_usd),
                    volume_usd = EXCLUDED.volume_usd,
                    tx_count = EXCLUDED.tx_count;
            """, (pool_id, d['date'], d['tx_count'], d['volume_usd'], d['tvl_usd']))
            
        conn.commit()
    
    cur.close()
    conn.close()

with DAG(
    'uniswap_v4_history_sync',
    default_args=default_args,
    description='Derived daily history for Uniswap V4 Pools',
    schedule='0 1 * * *', # Daily at 1 AM
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['defi', 'uniswap', 'derived'],
) as dag:

    t1 = sync_pools_from_swaps()
    t2 = sync_v4_pool_ids()
    t3 = build_daily_history()
    t4 = sync_tvl_from_graph()

    t1 >> t2 >> t3 >> t4
