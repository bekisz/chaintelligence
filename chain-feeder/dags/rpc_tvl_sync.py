"""RPC-based TVL sync for all liquidity pools across all networks and protocols.

Queries on-chain balances via Multicall3 / eth_getStorageAt to get the real
reserves for each pool, computes USD TVL from token prices, and upserts into
liquidity_pool_history. This bypasses The Graph, which often reports 0 TVL for
stablecoin pools and other high-liquidity pairs.

- V3 pools: pool address derived via CREATE2, then slot0() + liquidity() called
  directly on the pool contract.
- V4 pools: PoolManager storage read (same as backfill_v4_tvl.py / sync_tvl_from_onchain).

Runs daily at 3 AM. Only touches today's date + forward-fills past 90 days.
"""
import sys
import os
import logging
from datetime import datetime, timezone

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ROUTING_DIR = os.path.join(ROOT_DIR, 'routing')
if ROUTING_DIR not in sys.path:
    sys.path.insert(0, ROUTING_DIR)

logger = logging.getLogger(__name__)

MAX_TVL = 5_000_000_000
BATCH_SIZE = 100
FORWARD_FILL_DAYS = 90

POOL_MANAGERS = {
    "Ethereum": "0x000000000004444c5dc75cb358380d2e3de08a90",
    "Arbitrum": "0x360e68faccca8ca495c1b759fd9eee466db9fb32",
    "Optimism": "0x9a13f98cb987694c9f086b1f5eb990eea8264ec3",
    "Base": "0x498581ff718922c3f8e6a244956af099b2652b2b",
    "Polygon": "0x67366782805870060151383f4bbff9dab53e5cd6",
}
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"

SIG_SLOT0 = "0x3850c7bd"
SIG_LIQUIDITY = "0x1a686502"

_SEL_DECIMALS = "0x313ce567"

_KOWN_DECIMALS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,
    "0x6b175474e89094c44da98b954eedeac495271d0f": 18,
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18,
    "0x0000000000000000000000000000000000000000": 18,
}

_RPC_URLS = {
    "Ethereum": [
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
        "https://ethereum-rpc.publicnode.com",
    ],
    "Arbitrum": ["https://arb1.arbitrum.io/rpc", "https://arbitrum.llamarpc.com"],
    "Base": ["https://mainnet.base.org", "https://base.llamarpc.com"],
    "Optimism": ["https://mainnet.optimism.io"],
    "Polygon": ["https://polygon-rpc.com"],
    "BNB": ["https://bsc-dataseed.binance.org", "https://bsc-dataseed1.binance.org"],
}


def get_db_connection():
    try:
        from airflow.providers.postgres.hooks.postgres import PostgresHook
        pg_hook = PostgresHook(postgres_conn_id='chaintelligence_db')
        return pg_hook.get_conn()
    except Exception:
        import psycopg2
        from config import DATA_WAREHOUSE_DB
        return psycopg2.connect(DATA_WAREHOUSE_DB)


def _rpc_urls(network):
    env_key = f"RPC_URL_{network.upper()}"
    urls = []
    env_val = os.environ.get(env_key)
    if env_val:
        urls.extend(u.strip() for u in env_val.split(",") if u.strip())
    env_generic = os.environ.get("RPC_URL")
    if env_generic and env_generic not in urls:
        urls.append(env_generic)
    for u in _RPC_URLS.get(network, _RPC_URLS["Ethereum"]):
        if u not in urls:
            urls.append(u)
    return urls


def call_rpc(method, params, network="Ethereum", retries=3):
    import requests, time
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    urls = _rpc_urls(network)
    last_err = None
    for _ in range(retries):
        for url in urls:
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if "result" in data:
                        return data["result"]
                    if "error" in data:
                        last_err = data["error"]
                else:
                    last_err = f"HTTP {resp.status_code}"
            except Exception as e:
                last_err = str(e)
                continue
        if last_err:
            time.sleep(1)
    logger.warning(f"RPC call failed: {method} {params}: {last_err}")
    return None


def call_rpc_batch(calls, network="Ethereum", retries=2):
    import requests
    urls = _rpc_urls(network)
    for url in urls:
        try:
            resp = requests.post(url, json=calls, timeout=30)
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list):
                    return [r.get("result") for r in results]
                if "result" in results:
                    return [results.get("result")]
        except Exception:
            continue
    return [None] * len(calls)


def fetch_decimals(addr, network="Ethereum"):
    addr_lower = addr.lower()
    if addr_lower in _KOWN_DECIMALS:
        return _KOWN_DECIMALS[addr_lower]
    res = call_rpc("eth_call", [{"to": addr, "data": _SEL_DECIMALS}, "latest"], network=network)
    if res and res != "0x":
        try:
            return int(res, 16)
        except ValueError:
            return 18
    return 18


def fetch_token_prices_defillama(network, addr0, addr1):
    import requests
    chain_map = {
        "Ethereum": "ethereum", "Arbitrum": "arbitrum", "Base": "base",
        "Optimism": "optimism", "Polygon": "polygon", "BNB": "bsc",
    }
    chain = chain_map.get(network, "ethereum")
    url = f"https://coins.llama.fi/prices/current/{chain}:{addr0},{chain}:{addr1}"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if resp.status_code == 200:
            coins = resp.json().get("coins", {})
            p0 = coins.get(f"{chain}:{addr0}", {}).get("price", 0)
            p1 = coins.get(f"{chain}:{addr1}", {}).get("price", 0)
            return p0, p1
    except Exception as e:
        logger.warning(f"DeFiLlama price fetch failed: {e}")
    return 0, 0


def _pools_storage_slot(pool_id_hex):
    from eth_hash.auto import keccak
    pool_id_bytes = bytes.fromhex(pool_id_hex.removeprefix("0x").lower())
    slot_bytes = (6).to_bytes(32, "big")
    return int.from_bytes(keccak(pool_id_bytes + slot_bytes), "big")


def _decode_slot0(hex_val):
    if not hex_val or hex_val == "0x":
        return 0, 0
    val = int(hex_val, 16)
    sqrt_price_x96 = val & ((1 << 160) - 1)
    tick_raw = (val >> 160) & ((1 << 24) - 1)
    tick = tick_raw - (1 << 24) if tick_raw >= (1 << 23) else tick_raw
    return sqrt_price_x96, tick


def _decode_liquidity(hex_val):
    if not hex_val or hex_val == "0x":
        return 0
    val = int(hex_val, 16)
    return val & ((1 << 128) - 1)


def _derive_v3_address(t0_bytes, t1_bytes, fee_val, factory_hex, init_hash_hex):
    from eth_hash.auto import keccak
    salt = keccak(b'\x00' * 12 + t0_bytes + b'\x00' * 12 + t1_bytes + fee_val.to_bytes(32, 'big'))
    f_bytes = bytes.fromhex(factory_hex.removeprefix('0x'))
    ih_bytes = bytes.fromhex(init_hash_hex.removeprefix('0x'))
    return '0x' + keccak(b'\xff' + f_bytes + salt + ih_bytes)[12:].hex()


def _resolve_dex_config(protocol_name, network):
    """Map a protocol name + network to (factory_address, init_hash) from dex-config.yaml."""
    import yaml
    config_path = os.path.join(ROOT_DIR, "..", "config", "dex-config.yaml")
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "dex-config.yaml")
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
    except Exception:
        logger.warning("Cannot load dex-config.yaml")
        return None, None

    proto_lower = protocol_name.lower().replace(" ", "_")
    net_lower = network.lower()
    if net_lower == "bnb":
        net_lower = "bsc"

    proto_cfg = cfg.get(proto_lower)
    if not proto_cfg:
        return None, None

    net_cfg = proto_cfg.get(net_lower)
    if not net_cfg:
        return None, None

    return net_cfg.get("factory"), net_cfg.get("init_hash")


def load_pools(conn):
    """Load all pools that have pool_address (V3) or pool_id (V4) with token info."""
    cur = conn.cursor()
    cur.execute("""
        SELECT lp.id, c0.symbol, c1.symbol, lp.fee_bps,
               ch.name AS network, pr.name AS protocol,
               lp.pool_address, lp.pool_id,
               c0c.contract_address AS c0_addr,
               c1c.contract_address AS c1_addr,
               c0.decimals AS c0_dec, c1.decimals AS c1_dec
        FROM liquidity_pool lp
        JOIN chain ch ON lp.chain_id = ch.id
        JOIN protocol pr ON lp.protocol_id = pr.id
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        JOIN coin_contract c0c ON c0c.coin_id = c0.coin_id AND c0c.chain_id = ch.id
        JOIN coin_contract c1c ON c1c.coin_id = c1.coin_id AND c1c.chain_id = ch.id
        WHERE (lp.pool_address IS NOT NULL OR lp.pool_id IS NOT NULL)
          AND c0c.contract_address IS NOT NULL
          AND c1c.contract_address IS NOT NULL
    """)
    rows = cur.fetchall()
    cur.close()
    return rows


def fetch_v3_pool_data(network, pool_address, c0_addr, c1_addr, d0, d1, p0, p1):
    """Fetch slot0 + liquidity from a V3 pool contract and compute TVL."""
    calls = [
        {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pool_address, "data": SIG_SLOT0}, "latest"], "id": 0},
        {"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": pool_address, "data": SIG_LIQUIDITY}, "latest"], "id": 1},
    ]
    results = call_rpc_batch(calls, network=network)
    if not results or len(results) < 2:
        return None

    slot0_hex = results[0]
    liq_hex = results[1]
    if not slot0_hex or slot0_hex in ("0x", "0x" + "0" * 64):
        return None

    sqrt_price_x96, tick = _decode_slot0(slot0_hex)
    liquidity = _decode_liquidity(liq_hex)
    if sqrt_price_x96 == 0:
        return None

    if liquidity == 0:
        return 0.0

    return compute_tvl(sqrt_price_x96, liquidity, d0, d1, p0, p1, tick)


def fetch_v4_pool_data(network, pool_id_hex, d0, d1, p0, p1):
    """Fetch PoolManager storage slot for a V4 pool and compute TVL."""
    pool_manager = POOL_MANAGERS.get(network)
    if not pool_manager:
        logger.debug(f"No PoolManager address configured for {network}")
        return None
    base_slot = _pools_storage_slot(pool_id_hex)
    calls = [
        {"jsonrpc": "2.0", "method": "eth_getStorageAt", "params": [pool_manager, hex(base_slot), "latest"], "id": 0},
        {"jsonrpc": "2.0", "method": "eth_getStorageAt", "params": [pool_manager, hex(base_slot + 3), "latest"], "id": 1},
    ]
    results = call_rpc_batch(calls, network=network)
    if not results or len(results) < 2:
        return None

    slot0_hex = results[0]
    liq_hex = results[1]
    if not slot0_hex or slot0_hex in ("0x", "0x" + "0" * 64):
        return None

    sqrt_price_x96, tick = _decode_slot0(slot0_hex)
    liquidity = _decode_liquidity(liq_hex)
    if sqrt_price_x96 == 0:
        return None

    if liquidity == 0:
        return 0.0

    return compute_tvl(sqrt_price_x96, liquidity, d0, d1, p0, p1, tick)


def compute_tvl(sqrt_price_x96, liquidity, d0, d1, p0, p1, tick=None):
    if tick is not None and abs(tick) > 500000:
        logger.info(f"    Skipping pool: tick={tick} (out of range)")
        return None

    r0_raw = liquidity * (1 << 96) // sqrt_price_x96
    r1_raw = liquidity * sqrt_price_x96 // (1 << 96)

    amount0 = r0_raw / (10 ** d0)
    amount1 = r1_raw / (10 ** d1)

    if amount0 <= 0 or amount1 <= 0:
        return None

    tvl_usd = round(amount0 * p0 + amount1 * p1, 2)
    if tvl_usd <= 0 or tvl_usd > MAX_TVL:
        return None
    return tvl_usd


def group_pools_by_network(pools):
    """Group pool rows by network, returning structured dicts."""
    by_network = {}
    for p in pools:
        pool_db_id, c0_sym, c1_sym, fee_bps, network, protocol, pool_address, pool_id, c0_addr, c1_addr, c0_dec, c1_dec = p
        network = network.capitalize() if network else network
        entry = {
            "pool_db_id": pool_db_id,
            "c0_sym": c0_sym,
            "c1_sym": c1_sym,
            "protocol": protocol,
            "fee_bps": fee_bps,
            "pool_address": pool_address,
            "pool_id": pool_id,
            "c0_addr": c0_addr.lower() if c0_addr else None,
            "c1_addr": c1_addr.lower() if c1_addr else None,
            "c0_dec": c0_dec or 18,
            "c1_dec": c1_dec or 18,
        }
        by_network.setdefault(network, []).append(entry)

    # Sort V3 pools first (have CREATE2 addresses, easier to batch)
    for net in by_network:
        by_network[net].sort(key=lambda x: 0 if x["pool_address"] else 1)
    return by_network


def upsert_tvl(conn, pool_db_id, date_val, tvl_usd):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO liquidity_pool_history (pool_id, date, tvl_usd)
           VALUES (%s, %s, %s)
           ON CONFLICT (pool_id, date) DO UPDATE
           SET tvl_usd = CASE
               WHEN EXCLUDED.tvl_usd IS NOT NULL AND EXCLUDED.tvl_usd > 1.0 THEN EXCLUDED.tvl_usd
               WHEN liquidity_pool_history.tvl_usd IS NOT NULL AND liquidity_pool_history.tvl_usd > 0 THEN liquidity_pool_history.tvl_usd
               ELSE GREATEST(0, COALESCE(EXCLUDED.tvl_usd, 0))
           END""",
        (pool_db_id, date_val, tvl_usd),
    )
    cur.close()
    conn.commit()


def forward_fill_tvl(conn, pool_db_id, max_days=90):
    cur = conn.cursor()
    cur.execute(
        """UPDATE liquidity_pool_history lph
           SET tvl_usd = (
               SELECT lph2.tvl_usd
               FROM liquidity_pool_history lph2
               WHERE lph2.pool_id = %s
                 AND lph2.date = CURRENT_DATE
                 AND lph2.tvl_usd IS NOT NULL
                 AND lph2.tvl_usd > 0
               LIMIT 1
           )
           WHERE lph.pool_id = %s
             AND lph.date >= CURRENT_DATE - INTERVAL '%s days'
             AND lph.date < CURRENT_DATE
             AND (lph.tvl_usd IS NULL OR lph.tvl_usd <= 0)""",
        (pool_db_id, pool_db_id, max_days),
    )
    filled = cur.rowcount
    cur.close()
    conn.commit()
    return filled


def process_v3_pool(entry, network, conn, today):
    """Process a single V3 pool: derive address, fetch on-chain data, upsert."""
    factory, init_hash = _resolve_dex_config(entry["protocol"], network)
    if not factory or not init_hash:
        logger.debug(f"No dex config for {entry['protocol']}/{network}, skipping pool {entry['pool_db_id']}")
        return None

    fee_val = int(float(entry["fee_bps"]) * 100) if entry["fee_bps"] else 3000

    pool_address = entry["pool_address"]
    if not pool_address:
        t0_bytes = bytes.fromhex(entry["c0_addr"].removeprefix("0x").zfill(64))
        t1_bytes = bytes.fromhex(entry["c1_addr"].removeprefix("0x").zfill(64))
        pool_address = _derive_v3_address(t0_bytes[:20], t1_bytes[:20], fee_val, factory, init_hash)

    c0_addr = entry["c0_addr"]
    c1_addr = entry["c1_addr"]

    d0 = entry["c0_dec"] or fetch_decimals(c0_addr, network)
    d1 = entry["c1_dec"] or fetch_decimals(c1_addr, network)
    p0, p1 = fetch_token_prices_defillama(network, c0_addr, c1_addr)
    if p0 == 0 or p1 == 0:
        return None

    tvl = fetch_v3_pool_data(network, pool_address, c0_addr, c1_addr, d0, d1, p0, p1)
    if tvl is None:
        return None

    upsert_tvl(conn, entry["pool_db_id"], today, tvl)
    filled = forward_fill_tvl(conn, entry["pool_db_id"], FORWARD_FILL_DAYS) if tvl > 0 else 0
    return tvl, filled


def process_v4_pool(entry, network, conn, today):
    """Process a single V4 pool: read PoolManager storage, compute TVL, upsert."""
    pool_id_hex = entry["pool_id"]
    if not pool_id_hex:
        return None

    c0_addr = entry["c0_addr"]
    c1_addr = entry["c1_addr"]

    d0 = entry["c0_dec"] or fetch_decimals(c0_addr, network)
    d1 = entry["c1_dec"] or fetch_decimals(c1_addr, network)
    p0, p1 = fetch_token_prices_defillama(network, c0_addr, c1_addr)
    if p0 == 0 or p1 == 0:
        return None

    tvl = fetch_v4_pool_data(network, pool_id_hex, d0, d1, p0, p1)
    if tvl is None:
        return None

    upsert_tvl(conn, entry["pool_db_id"], today, tvl)
    filled = forward_fill_tvl(conn, entry["pool_db_id"], FORWARD_FILL_DAYS) if tvl > 0 else 0
    return tvl, filled


def run_rpc_tvl_sync():
    """Main entry point: load pools, group by network, process each."""
    conn = get_db_connection()
    try:
        pools = load_pools(conn)
        logger.info(f"Loaded {len(pools)} pools with on-chain addresses")
        if not pools:
            logger.info("Nothing to do")
            return

        by_network = group_pools_by_network(pools)
        today = datetime.now(timezone.utc).date()
        total_processed = 0
        total_filled = 0

        for network, net_pools in by_network.items():
            v3_pools = [p for p in net_pools if "V4" not in p["protocol"]]
            v4_pools = [p for p in net_pools if "V4" in p["protocol"] and p["pool_id"]]
            logger.info(f"  {network}: {len(v3_pools)} V3 pools, {len(v4_pools)} V4 pools")

            for pool_entry in v3_pools:
                result = process_v3_pool(pool_entry, network, conn, today)
                if result:
                    tvl, filled = result
                    logger.info(f"    V3 pool {pool_entry['pool_db_id']} ({pool_entry['c0_sym']}/{pool_entry['c1_sym']}): ${tvl:,.2f} TVL, fwd-filled {filled} days")
                    total_processed += 1
                    total_filled += filled

            for pool_entry in v4_pools:
                result = process_v4_pool(pool_entry, network, conn, today)
                if result:
                    tvl, filled = result
                    logger.info(f"    V4 pool {pool_entry['pool_db_id']} ({pool_entry['c0_sym']}/{pool_entry['c1_sym']}): ${tvl:,.2f} TVL, fwd-filled {filled} days")
                    total_processed += 1
                    total_filled += filled

        logger.info(f"RPC TVL sync complete. Processed: {total_processed}, Fwd-filled days: {total_filled}")
    finally:
        conn.close()


try:
    from airflow import DAG
    from airflow.sdk import task
    import pendulum
    from datetime import timedelta

    default_args = {
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': False,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    }

    @task
    def execute_rpc_tvl_sync_task():
        return run_rpc_tvl_sync()

    with DAG(
        'rpc_tvl_sync',
        max_active_runs=1,
        default_args=default_args,
        description='RPC-based TVL sync for all pools across all networks',
        schedule='0 3 * * *',
        start_date=pendulum.now().subtract(days=1),
        catchup=False,
        tags=['defi', 'tvl', 'rpc'],
    ) as dag:
        execute_rpc_tvl_sync_task()

except ImportError:
    pass

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_rpc_tvl_sync()