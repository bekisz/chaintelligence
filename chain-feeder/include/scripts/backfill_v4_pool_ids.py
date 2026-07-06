#!/usr/bin/env python3
"""
One-shot backfill: fetch poolId from the V4 subgraph for all existing
Uniswap V4 liquidity_pool rows where pool_id IS NULL, and UPDATE them.

Usage:
    python backfill_v4_pool_ids.py

Requires:
    - DATA_WAREHOUSE_DB env var or default local connection
    - GRAPH_API_KEY env var (or The Graph gateway access)
    - Same Python deps as the DAG (requests, psycopg2, etc.)
"""

import os
import sys
import logging
from datetime import datetime, timezone
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Add the DAG's common utils to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'chain-feeder', 'dags'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'chain-feeder', 'routing'))

from common.utils.uniswap_utils import UniswapV4Fetcher
from config import DATA_WAREHOUSE_DB
import psycopg2


def main():
    logging.info("Connecting to DB...")
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()

    # 1. Build symbol→address map by network (same strategy as the DAG)
    logging.info("Building token address map from coin_contract + swap history...")
    symbol_map = defaultdict(dict)

    cur.execute("""
        SELECT cc.chain, c.symbol, cc.contract_address
        FROM coin_contract cc
        JOIN coin c ON cc.coin_id = c.coin_id
    """)
    for chain, sym, addr in cur.fetchall():
        if sym and addr:
            symbol_map[chain.capitalize()][sym.upper()] = addr.lower()

    cur.execute("""
        SELECT network, sym, addr, SUM(c) as total_c FROM (
            SELECT network, token0_symbol as sym, token0_address as addr,
                   count(*) as c FROM uniswap_v4_swaps GROUP BY 1, 2, 3
            UNION ALL
            SELECT network, token1_symbol as sym, token1_address as addr,
                   count(*) as c FROM uniswap_v4_swaps GROUP BY 1, 2, 3
        ) t GROUP BY 1, 2, 3 ORDER BY total_c ASC
    """)
    for network, sym, addr, count in cur.fetchall():
        if sym and addr:
            symbol_map[network][sym.upper()] = addr.lower()
            if len(sym) > 8:
                symbol_map[network][sym[:8].upper()] = addr.lower()

    # 2. Get V4 pools missing pool_id
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
        cur.close()
        conn.close()
        return

    # 3. Query subgraph per pool
    updated = 0
    skipped = 0
    for pool_db_id, c0, c1, fee, network in missing:
        if not fee:
            skipped += 1
            continue

        net_symbol_map = symbol_map.get(network, {})
        addr0 = net_symbol_map.get(c0.upper())
        addr1 = net_symbol_map.get(c1.upper())

        if not addr0 or not addr1:
            logging.warning(
                f"  Skipping pool {pool_db_id} ({c0}-{c1}) on {network}: "
                "token address not found"
            )
            skipped += 1
            continue

        # Normalize fee to bips
        try:
            if fee == 'Dynamic':
                fee_bips = 8388608
            elif fee.isdigit():
                fee_bips = int(fee)
            else:
                fee_bips = int(round(float(fee.replace('%', '').strip()) * 10000))
        except (ValueError, AttributeError):
            skipped += 1
            continue

        fetcher = UniswapV4Fetcher(verbose=False, network=network)
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
        if not result or 'data' not in result:
            skipped += 1
            continue

        pools_data = result['data'].get('pools', [])
        if not pools_data:
            skipped += 1
            continue

        pool_id = pools_data[0].get('id')
        if not pool_id:
            skipped += 1
            continue

        cur.execute(
            "UPDATE liquidity_pool SET pool_id = %s WHERE id = %s",
            (pool_id, pool_db_id)
        )
        updated += 1
        logging.info(f"  Updated pool {pool_db_id} ({c0}-{c1}, {network}, fee={fee}) → {pool_id}")

    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Done: {updated} updated, {skipped} skipped")


if __name__ == '__main__':
    main()