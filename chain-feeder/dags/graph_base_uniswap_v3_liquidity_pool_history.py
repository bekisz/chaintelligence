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
    logging.info("Dynamic self-healing ingestion handles pool creation during swap ingestion. Skipping legacy sync.")
    return 0

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
        s.pool_id AS pool_id,
        DATE(s.ts) AS date,
        COUNT(*) AS tx_count,
        SUM(s.amount_usd) AS volume_usd
    FROM swaps s
    JOIN liquidity_pool lp ON s.pool_id = lp.id
    JOIN chain ch ON lp.chain_id = ch.id
    JOIN protocol pr ON lp.protocol_id = pr.id
    WHERE s.amount_usd IS NOT NULL
      AND LOWER(ch.name) = LOWER(%(network)s)
      AND pr.name IN ('Uniswap V3', 'PancakeSwap V3', 'Aerodrome')
    GROUP BY s.pool_id, DATE(s.ts)
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
        JOIN chain ch ON cc.chain_id = ch.id
        WHERE LOWER(ch.name) = LOWER(%s) AND cc.contract_address IS NOT NULL
    """, (NETWORK,))
    for sym, addr in cur.fetchall():
        if sym and addr:
            symbol_map[NETWORK][sym.upper()] = addr.lower()
            if len(sym) > 8:
                symbol_map[NETWORK][sym[:8].upper()] = addr.lower()

    cur.execute("""
        SELECT lp.id, c0.symbol, c1.symbol, lp.fee_bps, ch.name AS network, pr.name AS protocol
        FROM liquidity_pool lp
        JOIN chain ch ON lp.chain_id = ch.id
        JOIN protocol pr ON lp.protocol_id = pr.id
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE pr.name IN ('Uniswap V3', 'PancakeSwap V3', 'Aerodrome')
          AND LOWER(ch.name) = LOWER(%s)
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
    max_active_runs=1,
    default_args=default_args,
    description='Derived daily history for V3 pools on Ethereum',
    schedule='0 1 * * *',
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['defi', 'uniswap', 'derived'],
) as dag:
    sync_pools_from_swaps() >> build_daily_history() >> sync_tvl_from_graph()