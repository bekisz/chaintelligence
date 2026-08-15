"""Backfill V4 pool on-chain TVL into liquidity_pool_daily_stats.

Reads PoolManager storage via RPC to get current sqrtPriceX96 + liquidity,
computes USD TVL, and fills missing days in liquidity_pool_daily_stats.

Usage:
    python include/scripts/backfill_v4_tvl.py
    python include/scripts/backfill_v4_tvl.py --dry-run
    python include/scripts/backfill_v4_tvl.py --pools 12934,4824
    python include/scripts/backfill_v4_tvl.py --network Ethereum
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from v4_tvl_fetcher import (
    _pools_storage_slot, _decode_slot0, _decode_liquidity,
    call_rpc_batch, call_rpc, fetch_decimals, fetch_token_prices_defillama,
    POOL_MANAGER,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_v4_tvl")


def db_connect():
    import psycopg2
    dsn = os.environ.get(
        "DATA_WAREHOUSE_DB",
        "host=localhost port=5433 dbname=chaintelligence user=airflow password=airflow",
    )
    return psycopg2.connect(dsn)


def get_v4_pools(conn, pool_ids=None, network=None):
    cur = conn.cursor()
    clauses = ["pr.name = 'Uniswap V4'", "lp.pool_id IS NOT NULL"]
    params = []
    if pool_ids:
        clauses.append("lp.id = ANY(%s)")
        params.append(pool_ids)
    if network:
        clauses.append("ch.name = %s")
        params.append(network)
    cur.execute(
        f"""SELECT lp.id, c0.symbol, c1.symbol, lp.fee_bps, lp.pool_id,
                   ch.name AS network, c0c.contract_address AS c0_addr,
                   c1c.contract_address AS c1_addr
            FROM liquidity_pool lp
            JOIN chain ch ON lp.chain_id = ch.id
            JOIN protocol pr ON lp.protocol_id = pr.id
            JOIN coin c0 ON lp.coin0_id = c0.coin_id
            JOIN coin c1 ON lp.coin1_id = c1.coin_id
            JOIN coin_contract c0c ON c0c.coin_id = c0.coin_id AND c0c.chain_id = ch.id
            JOIN coin_contract c1c ON c1c.coin_id = c1.coin_id AND c1c.chain_id = ch.id
            WHERE {' AND '.join(clauses)}""",
        params,
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def upsert_tvl(conn, pool_db_id, date_val, tvl_usd):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO liquidity_pool_daily_stats (pool_id, day, tvl_usd)
           VALUES (%s, %s, %s)
           ON CONFLICT (pool_id, day) DO UPDATE
           SET tvl_usd = CASE
               WHEN EXCLUDED.tvl_usd IS NOT NULL AND EXCLUDED.tvl_usd > 1.0 THEN EXCLUDED.tvl_usd
               WHEN liquidity_pool_daily_stats.tvl_usd IS NOT NULL AND liquidity_pool_daily_stats.tvl_usd > 0 THEN liquidity_pool_daily_stats.tvl_usd
               ELSE GREATEST(0, COALESCE(EXCLUDED.tvl_usd, 0))
           END""",
        (pool_db_id, date_val, tvl_usd),
    )
    cur.close()
    conn.commit()


def forward_fill_tvl(conn, pool_db_id, max_days=90):
    cur = conn.cursor()
    cur.execute(
        """UPDATE liquidity_pool_daily_stats lph
           SET tvl_usd = (
               SELECT lph2.tvl_usd
               FROM liquidity_pool_daily_stats lph2
               WHERE lph2.pool_id = %s
                 AND lph2.day = CURRENT_DATE
                 AND lph2.tvl_usd IS NOT NULL
                 AND lph2.tvl_usd > 0
               LIMIT 1
           )
           WHERE lph.pool_id = %s
             AND lph.day >= CURRENT_DATE - INTERVAL '%s days'
             AND lph.day < CURRENT_DATE
             AND (lph.tvl_usd IS NULL OR lph.tvl_usd <= 0)""",
        (pool_db_id, pool_db_id, max_days),
    )
    filled = cur.rowcount
    cur.close()
    conn.commit()
    return filled


def process_pool(p, slot0_hex, liq_hex, conn, today, dry_run, max_days):
    pool_db_id, c0_sym, c1_sym, fee_bps, pool_id_hex, network, c0_addr, c1_addr = p

    if not slot0_hex or slot0_hex in ("0x", "0x" + "0" * 64):
        return (pool_db_id, 0, 0, "no storage")

    sqrt_price_x96, tick = _decode_slot0(slot0_hex)
    liquidity = _decode_liquidity(liq_hex)

    if sqrt_price_x96 == 0 or liquidity == 0:
        return (pool_db_id, 0, 0, "no liquidity")

    d0 = fetch_decimals(c0_addr, network)
    d1 = fetch_decimals(c1_addr, network)
    p0, p1 = fetch_token_prices_defillama(network, c0_addr, c1_addr)
    if p0 == 0 or p1 == 0:
        return (pool_db_id, 0, liquidity, "no prices")

    sqrt_price = sqrt_price_x96 / (1 << 96)
    reserve0_raw = liquidity * (1 << 96) // sqrt_price_x96
    reserve1_raw = liquidity * sqrt_price_x96 // (1 << 96)

    amount0 = reserve0_raw / (10 ** d0)
    amount1 = reserve1_raw / (10 ** d1)
    tvl_usd = round(amount0 * p0 + amount1 * p1, 2)

    if tvl_usd <= 0:
        return (pool_db_id, 0, liquidity, "zero tvl")

    msg = f"Pool {pool_db_id} ({c0_sym}/{c1_sym}, fee={fee_bps}): ${tvl_usd:,.2f} TVL"
    if dry_run:
        logger.info(f"[DRY-RUN] {msg}")
        return (pool_db_id, tvl_usd, liquidity, None)

    upsert_tvl(conn, pool_db_id, today, tvl_usd)
    filled = forward_fill_tvl(conn, pool_db_id, max_days)
    if filled:
        msg += f", fwd-filled {filled} days"
    logger.info(msg)
    return (pool_db_id, tvl_usd, liquidity, None)


def main():
    parser = argparse.ArgumentParser(description="Backfill V4 on-chain TVL")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pools", type=str, help="Comma-separated pool DB IDs")
    parser.add_argument("--network", type=str)
    parser.add_argument("--max-days", type=int, default=90)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()

    pool_ids_filter = [int(p.strip()) for p in args.pools.split(",")] if args.pools else None

    conn = db_connect()
    pools = get_v4_pools(conn, pool_ids_filter, args.network)
    logger.info(f"Loaded {len(pools)} V4 pools with pool_id")

    if not pools:
        logger.info("Nothing to do")
        conn.close()
        return

    by_network = {}
    for p in pools:
        by_network.setdefault(p[5], []).append(p)

    today = datetime.now(timezone.utc).date()
    upserted = 0
    active = 0
    no_storage = 0
    no_liq = 0
    no_price = 0
    zero_tvl = 0

    for network, net_pools in by_network.items():
        logger.info(f"Processing {len(net_pools)} pools on {network}")

        pool_slots = [(p, _pools_storage_slot(p[4])) for p in net_pools]
        slot_tuples = [(p, s0, s0 + 3) for p, s0 in pool_slots]

        for i in range(0, len(slot_tuples), args.batch_size):
            batch = slot_tuples[i:i + args.batch_size]
            rpc_calls = []
            for idx, (p, s0, s3) in enumerate(batch):
                rpc_calls.append({
                    "jsonrpc": "2.0", "method": "eth_getStorageAt",
                    "params": [POOL_MANAGER, hex(s0), "latest"], "id": idx * 2,
                })
                rpc_calls.append({
                    "jsonrpc": "2.0", "method": "eth_getStorageAt",
                    "params": [POOL_MANAGER, hex(s3), "latest"], "id": idx * 2 + 1,
                })

            results = call_rpc_batch(rpc_calls, network=network)
            use_batch = results and len(results) == len(rpc_calls)

            for idx, (p, s0, s3) in enumerate(batch):
                if use_batch:
                    slot0_hex = results[idx * 2]
                    liq_hex = results[idx * 2 + 1]
                else:
                    slot0_hex = call_rpc("eth_getStorageAt", [POOL_MANAGER, hex(s0), "latest"], network=network)
                    liq_hex = call_rpc("eth_getStorageAt", [POOL_MANAGER, hex(s3), "latest"], network=network)

                pid, tvl, liq, err = process_pool(p, slot0_hex, liq_hex, conn, today, args.dry_run, args.max_days)
                if err == "no storage":
                    no_storage += 1
                elif err == "no liquidity":
                    no_liq += 1
                elif err == "no prices":
                    no_price += 1
                elif err == "zero tvl":
                    zero_tvl += 1
                else:
                    active += 1
                    upserted += 1

    conn.close()
    logger.info(
        f"Done. Active: {active}, Upserted today: {upserted}, "
        f"No-storage: {no_storage}, No-liq: {no_liq}, No-price: {no_price}, Zero-TVL: {zero_tvl}"
    )


if __name__ == "__main__":
    main()
