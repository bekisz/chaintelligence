from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
from datetime import datetime, timedelta, timezone
import logging

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

NETWORK = 'Base'

def get_base_asset_order(sym0, sym1):
    h0 = HARDNESS_MAP.get(sym0, 0)
    h1 = HARDNESS_MAP.get(sym1, 0)
    is_swapped = False
    if h0 > h1: is_swapped = True
    elif h0 == h1 and sym0 > sym1: is_swapped = True
    if is_swapped:
        return sym1, sym0
    else:
        return sym0, sym1

def normalize_fee_tier(fee_str):
    if not fee_str: return None
    if fee_str.isdigit(): return fee_str
    mapping = {
        '0.01%': '100', '0.05%': '500', '0.08%': '800',
        '0.3%': '3000', '1.0%': '10000'
    }
    return mapping.get(fee_str.strip(), fee_str)

@task
def sync_pools_from_swaps():
    """Scans the unified swaps table for V3 token pairings and creates pools."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    logging.info(f"Scanning swaps table for new V3 pools on {NETWORK}...")
    cur.execute("""
        SELECT DISTINCT s.network, c0.symbol, c1.symbol, s.fee_display, s.protocol
        FROM swaps s
        JOIN coin c0 ON s.t0_coin_id = c0.coin_id
        JOIN coin c1 ON s.t1_coin_id = c1.coin_id
        WHERE s.protocol IN ('Uniswap V3', 'PancakeSwap V3', 'Aerodrome')
          AND s.network = %s
    """, (NETWORK,))
    rows = cur.fetchall()

    new_pools = 0
    for network, s0, s1, fee, protocol in rows:
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
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (network, protocol, pool_name, fee_tier) DO NOTHING
            """, (network, protocol, pool_name, row0[0], row1[0], fee_bips))
            if cur.statusmessage.startswith("INSERT 0 1"):
                new_pools += 1
        except Exception as e:
            conn.rollback()
            logging.warning(f"Failed to sync pool {pool_name}: {e}")

    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Synced {new_pools} new V3 pools on {NETWORK}.")

@task
def build_daily_history():
    """Aggregates daily metrics from the unified swaps table into liquidity_pool_history."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    logging.info(f"Refreshing liquidity_pool_history for {NETWORK} from unified swaps...")

    query = """
    INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd)
    SELECT
        lp.id as pool_id,
        DATE(s.ts) as date,
        COUNT(*) as tx_count,
        SUM(s.amount_usd) as volume_usd
    FROM swaps s
    JOIN coin c0 ON s.t0_coin_id = c0.coin_id
    JOIN coin c1 ON s.t1_coin_id = c1.coin_id
    JOIN liquidity_pool lp ON
        lp.network = s.network
        AND lp.protocol = s.protocol
        AND lp.fee_tier = CASE
            WHEN s.fee_display IN ('0.01%%', '0.01') THEN '100'
            WHEN s.fee_display IN ('0.05%%', '0.05') THEN '500'
            WHEN s.fee_display IN ('0.08%%', '0.08') THEN '800'
            WHEN s.fee_display IN ('0.3%%', '0.3') THEN '3000'
            WHEN s.fee_display IN ('1.0%%', '1.0') THEN '10000'
            ELSE s.fee_display
        END
        AND (
            (lp.coin0_id = s.t0_coin_id AND lp.coin1_id = s.t1_coin_id)
            OR (lp.coin0_id = s.t1_coin_id AND lp.coin1_id = s.t0_coin_id)
        )
    WHERE s.amount_usd IS NOT NULL
      AND s.network = %(network)s
      AND s.protocol IN ('Uniswap V3', 'PancakeSwap V3', 'Aerodrome')
    GROUP BY lp.id, DATE(s.ts)
    ON CONFLICT (pool_id, date) DO UPDATE
    SET tx_count = EXCLUDED.tx_count,
        volume_usd = EXCLUDED.volume_usd;
    """
    cur.execute(query, {"network": NETWORK})
    updated_rows = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Updated {updated_rows} daily history records for {NETWORK}.")

@task
def sync_tvl_from_graph():
    """Fetches daily TVL from The Graph for V3 pools on this network."""
    from common.utils.uniswap_utils import UniswapV3Fetcher
    from collections import defaultdict

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    logging.info(f"Building symbol->address map for {NETWORK}...")
    symbol_map = defaultdict(dict)

    # Get addresses from coin_contract for this network
    cur.execute("""
        SELECT c.symbol, cc.contract_address
        FROM coin c
        JOIN coin_contract cc ON c.coin_id = cc.coin_id
        WHERE cc.chain = %s AND cc.contract_address IS NOT NULL
    """, (NETWORK.lower(),))
    for sym, addr in cur.fetchall():
        if sym and addr:
            symbol_map[NETWORK][sym.upper()] = addr.lower()
            if len(sym) > 8:
                symbol_map[NETWORK][sym[:8].upper()] = addr.lower()

    cur.execute("""
        SELECT lp.id, c0.symbol, c1.symbol, lp.fee_tier, lp.network, lp.protocol
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE lp.protocol IN ('Uniswap V3', 'PancakeSwap V3', 'Aerodrome')
          AND lp.network = %s
    """, (NETWORK,))
    pools = cur.fetchall()

    fetchers = {}
    for pool in pools:
        pool_id, c0, c1, fee, network, protocol = pool
        if not fee: continue
        net_symbol_map = symbol_map[network]
        addr0 = net_symbol_map.get(c0.upper())
        addr1 = net_symbol_map.get(c1.upper())
        if not addr0 or not addr1:
            logging.warning(f"Skipping pool {pool_id} ({c0}-{c1}) on {network}: address not found")
            continue
        try:
            fee_bips = int(fee)
        except:
            continue
        start_date = datetime.now(timezone.utc) - timedelta(days=90)
        fetcher_key = (network, protocol)
        if fetcher_key not in fetchers:
            fetchers[fetcher_key] = UniswapV3Fetcher(verbose=True, network=network, protocol=protocol)
        fetcher = fetchers[fetcher_key]
        try:
            data = fetcher.fetch_pool_daily_data(addr0, addr1, fee_bips, start_date)
        except Exception as e:
            logging.error(f"Graph fetch failed for {c0}-{c1} on {network}: {e}")
            continue
        if not data:
            continue
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
    'graph_base_uniswap_v3_liquidity_pool_history',
    default_args=default_args,
    description='Derived daily history for V3 pools on Ethereum',
    schedule='0 1 * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['defi', 'uniswap', 'derived'],
) as dag:
    sync_pools_from_swaps() >> build_daily_history() >> sync_tvl_from_graph()