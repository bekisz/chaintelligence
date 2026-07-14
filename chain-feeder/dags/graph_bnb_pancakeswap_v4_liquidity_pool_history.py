"""
PancakeSwap V4 (Infinity) — derived pool + daily history sync.

Reads PancakeSwap V4 swaps from the unified `swaps` table (protocol =
'PancakeSwap V4', network = 'BNB') and:
  1. sync_pools_from_swaps — ensures every distinct (network, coin0, coin1, fee)
     pairing exists in `liquidity_pool` with protocol = 'PancakeSwap V4'.
  2. sync_v4_pool_ids — backfills `liquidity_pool.pool_id` (the V4 poolId) by
     querying the PancakeSwap V4 BNB subgraph.
  3. build_daily_history — aggregates swaps into `liquidity_pool_history`.

This mirrors `uniswap_v4_history_sync.py` but sources from the unified `swaps`
table (the legacy `uniswap_v4_swaps` table no longer exists) and uses the
coin_id FKs stored on swaps rather than symbol-truncation heuristics.
"""
from airflow import DAG
from airflow.sdk import task
from airflow.providers.postgres.hooks.postgres import PostgresHook
import pendulum
import logging

PROTOCOL = 'PancakeSwap V4'

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': pendulum.Duration(minutes=5),
}

# Stable base-asset ordering so pool_name matches across protocols/swaps.
HARDNESS_MAP = {
    'USDC': 1000, 'USDT': 990, 'DAI': 970, 'FDUSD': 965, 'BUSD': 960,
    'WBNB': 950, 'BNB': 950, 'WBTC': 870, 'BTCB': 870,
    'WETH': 860, 'ETH': 860, 'CAKE': 700,
}


def get_base_asset_order(sym0, sym1):
    h0 = HARDNESS_MAP.get(sym0, 0)
    h1 = HARDNESS_MAP.get(sym1, 0)
    if h0 > h1 or (h0 == h1 and sym0 > sym1):
        return sym1, sym0
    return sym0, sym1


def normalize_fee_tier(fee_str):
    """Normalize a fee display string (e.g. '0.01%') to bips string (e.g. '100')."""
    if not fee_str:
        return None
    s = fee_str.strip()
    if s == 'Dynamic':
        return '8388608'
    if s.isdigit():
        return s
    mapping = {'0.01%': '100', '0.05%': '500', '0.08%': '800',
               '0.25%': '2500', '0.3%': '3000', '1.0%': '10000'}
    return mapping.get(s, s)


@task
def sync_pools_from_swaps():
    """Ensure liquidity_pool has a row per distinct PancakeSwap V4 swap pairing."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT s.network, c0.symbol, c1.symbol, s.fee_display
        FROM swaps s
        JOIN coin c0 ON s.t0_coin_id = c0.coin_id
        JOIN coin c1 ON s.t1_coin_id = c1.coin_id
        WHERE s.protocol = %s
    """, (PROTOCOL,))
    rows = cur.fetchall()

    new_pools = 0
    for network, s0, s1, fee_display in rows:
        if not s0 or not s1:
            continue
        c0, c1 = get_base_asset_order(s0.upper(), s1.upper())
        pool_name = f"{c0} - {c1}"
        fee_bips = normalize_fee_tier(fee_display)

        cur.execute("SELECT coin_id FROM coin WHERE UPPER(symbol) = %s", (c0,))
        row0 = cur.fetchone()
        cur.execute("SELECT coin_id FROM coin WHERE UPPER(symbol) = %s", (c1,))
        row1 = cur.fetchone()
        if not row0 or not row1:
            continue

        try:
            cur.execute("""
                INSERT INTO liquidity_pool
                    (network, protocol, pool_name, coin0_id, coin1_id, fee_tier)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (network, protocol, pool_name, fee_tier) DO NOTHING
            """, (network, PROTOCOL, pool_name, row0[0], row1[0], fee_bips))
            if cur.statusmessage.startswith("INSERT 0 1"):
                new_pools += 1
        except Exception as e:
            conn.rollback()
            logging.warning(f"Failed to sync pool {pool_name} on {network}: {e}")

    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Synced {new_pools} new PancakeSwap V4 pools from swaps.")


@task
def sync_v4_pool_ids():
    """Backfill liquidity_pool.pool_id with the canonical 32-byte V4 poolId.

    The PancakeSwap V4 subgraph only exposes a 20-byte id that the Infinity info
    site does not accept, and the 32-byte poolId can't be derived without
    tickSpacing/hooks. The PancakeSwap explorer API, however, returns the 32-byte
    id for each pool. We query it per (network, token0) and match our pools by
    token pair + fee percentage.

    Explorer fee encoding: feeTier/6700 = percent (e.g. 335 -> 0.05%).
    Our fee_tier is bips (Uniswap encoding): int/10000 = percent (e.g. 500 -> 0.05%).
    """
    import requests
    from collections import defaultdict

    EXPLORER = "https://explorer.pancakeswap.com/api/cached/pools/infinity/{net}/list/top?token={addr}"
    # network name -> explorer chain segment
    NET_MAP = {"BNB": "bsc", "Ethereum": "eth", "Arbitrum": "arb", "Base": "base"}

    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    # symbol -> address per network (coin_contract)
    symbol_map = defaultdict(dict)
    cur.execute("""
        SELECT LOWER(cc.chain), UPPER(c.symbol), cc.contract_address
        FROM coin_contract cc JOIN coin c ON cc.coin_id = c.coin_id
    """)
    for chain, sym, addr in cur.fetchall():
        if sym and addr:
            net = 'BNB' if chain == 'bsc' else chain.capitalize()
            symbol_map[net][sym] = addr.lower()

    # All PancakeSwap V4 pools (refresh pool_id with the canonical 32-byte id;
    # any stale 20-byte subgraph id is wrong for the link).
    cur.execute("""
        SELECT lp.id, UPPER(c0.symbol), UPPER(c1.symbol), lp.fee_tier, lp.network
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE lp.protocol = %s
        LIMIT 500
    """, (PROTOCOL,))
    pools = cur.fetchall()
    logging.info(f"Resolving pool_ids for {len(pools)} PancakeSwap V4 pools")

    # Cache explorer responses per (network, token0_address) to limit API calls.
    explorer_cache = {}

    def get_explorer_pools(network, token_addr):
        key = (network, token_addr.lower())
        if key in explorer_cache:
            return explorer_cache[key]
        net_seg = NET_MAP.get(network, network.lower())
        url = EXPLORER.format(net=net_seg, addr=token_addr)
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json() or []
        except Exception as e:
            logging.warning(f"Explorer API failed for {network} token {token_addr}: {e}")
            data = []
        explorer_cache[key] = data
        return data

    def our_fee_pct(fee):
        if not fee or fee in ('Dynamic', '8388608'):
            return None
        if fee.isdigit():
            return int(fee) / 10000.0
        try:
            return float(fee.replace('%', '').strip()) / 100.0
        except Exception:
            return None

    updated = 0
    for pool_db_id, s0, s1, fee, network in pools:
        net_map = symbol_map.get(network, {})
        addr0 = net_map.get(s0)
        addr1 = net_map.get(s1)
        if not addr0 or not addr1:
            continue

        # Query explorer by token0's address (currency0); match on token1 + fee.
        exp_pools = get_explorer_pools(network, addr0)
        target_pct = our_fee_pct(fee)
        best = None
        best_tvl = -1.0
        for ep in exp_pools:
            et0 = (ep.get('token0') or {}).get('id', '').lower()
            et1 = (ep.get('token1') or {}).get('id', '').lower()
            if {et0, et1} != {addr0.lower(), addr1.lower()}:
                continue
            # fee match
            if fee in ('Dynamic', '8388608'):
                if not ep.get('isDynamicFee'):
                    continue
            else:
                if target_pct is None:
                    continue
                exp_pct = (ep.get('feeTier') or 0) / 6700.0
                if abs(exp_pct - target_pct) > 0.001:
                    continue
            try:
                tvl = float(ep.get('tvlUSD') or 0)
            except Exception:
                tvl = 0.0
            if tvl > best_tvl:
                best_tvl = tvl
                best = ep.get('id')

        if best:
            cur.execute("UPDATE liquidity_pool SET pool_id = %s WHERE id = %s", (best, pool_db_id))
            updated += 1
        else:
            # No explorer match (long-tail pool): clear the stale 20-byte id so
            # the API falls back to a token-page link instead of a broken URL.
            cur.execute("UPDATE liquidity_pool SET pool_id = NULL WHERE id = %s", (pool_db_id,))

    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Updated {updated} PancakeSwap V4 pool rows with a 32-byte poolId.")


@task
def build_daily_history():
    """Aggregate PancakeSwap V4 swaps into liquidity_pool_history."""
    pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
    conn = pg_hook.get_conn()
    cur = conn.cursor()

    cur.execute(f"""
    INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd)
    SELECT
        p.id AS pool_id,
        DATE(s.ts) AS date,
        COUNT(*) AS tx_count,
        SUM(s.amount_usd) AS volume_usd
    FROM swaps s
    JOIN liquidity_pool p ON
        p.network = s.network AND p.protocol = '{PROTOCOL}' AND
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
    WHERE s.protocol = '{PROTOCOL}' AND s.amount_usd IS NOT NULL
    GROUP BY p.id, DATE(s.ts)
    ON CONFLICT (pool_id, date) DO UPDATE
    SET tx_count = EXCLUDED.tx_count,
        volume_usd = EXCLUDED.volume_usd;
    """)
    updated = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Updated {updated} PancakeSwap V4 daily history records.")


with DAG(
    'graph_bnb_pancakeswap_v4_liquidity_pool_history',
    default_args=default_args,
    description='Derived daily history for PancakeSwap V4 (Infinity) pools',
    schedule='0 1 * * *',  # Daily at 1 AM
    start_date=pendulum.now().subtract(days=1),
    catchup=False,
    tags=['defi', 'pancakeswap', 'v4', 'derived'],
) as dag:

    t1 = sync_pools_from_swaps()
    t2 = sync_v4_pool_ids()
    t3 = build_daily_history()

    t1 >> t2 >> t3