#!/usr/bin/env python3
"""
One-shot backfill: fetch poolId from the V4 subgraph for all existing
Uniswap V4 liquidity_pool rows where pool_id IS NULL, and UPDATE them.

This version batch-fetches ALL pools from each network's V4 subgraph
(paginated) then matches locally, rather than one query per pool.

Usage:
    export DATA_WAREHOUSE_DB="host=localhost port=5433 dbname=chaintelligence user=airflow password=airflow"
    python chain-feeder/include/scripts/backfill_v4_pool_ids.py
"""

import os
import sys
import logging
import time
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'routing'))

from common.utils.uniswap_utils import UniswapV4Fetcher
from config import DATA_WAREHOUSE_DB
import psycopg2


# Only these networks have V4 subgraphs configured
SUPPORTED_NETWORKS = {'Ethereum', 'Arbitrum', 'Base'}


def normalize_fee_to_bips(fee_str: str) -> str:
    """Convert a fee string (e.g. '0.05%', '5') to normalized bips (e.g. '500')."""
    ft = fee_str.replace('%', '').strip()
    fee_map = {'0.01': '100', '0.05': '500', '0.08': '800', '0.3': '3000', '1.0': '10000'}
    if ft in fee_map:
        return fee_map[ft]
    try:
        fv = float(ft)
        if fv > 0 and fv < 5:
            return str(int(fv * 10000))
        return str(int(fv))
    except (ValueError, TypeError):
        return fee_str


def fetch_all_v4_pools(fetcher: UniswapV4Fetcher) -> list:
    """
    Paginated fetch of ALL V4 pools from the subgraph.
    Returns list of {id, token0, token1, feeTier}.
    """
    all_pools = []
    skip = 0
    page_size = 1000
    max_pools = 50000  # safety limit

    while skip < max_pools:
        query = f"""
        {{
          pools(first: {page_size}, skip: {skip}) {{
            id
            token0 {{ id }}
            token1 {{ id }}
            feeTier
          }}
        }}
        """
        result = fetcher._execute_query(query)
        if not result or 'data' not in result:
            break

        pools = result['data'].get('pools', [])
        if not pools:
            break

        for p in pools:
            all_pools.append({
                'id': p['id'],
                'token0': p['token0']['id'],
                'token1': p['token1']['id'],
                'feeTier': p['feeTier'],
            })

        logging.info(f"  Fetched {len(all_pools)} pools so far...")
        if len(pools) < page_size:
            break
        skip += page_size
        time.sleep(0.3)  # rate limit buffer

    logging.info(f"  Total pools fetched: {len(all_pools)}")
    return all_pools


def main():
    logging.info("Connecting to DB...")
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    except Exception as e:
        logging.error(f"Failed to connect: {e}")
        sys.exit(1)
    cur = conn.cursor()

    # 1. Build symbol→address map from coin_contract (primary source)
    logging.info("Building token address map from coin_contract...")
    symbol_map = defaultdict(dict)

    cur.execute("""
        SELECT LOWER(cc.chain) as chain, UPPER(c.symbol) as sym, LOWER(cc.contract_address) as addr
        FROM coin_contract cc
        JOIN coin c ON cc.coin_id = c.coin_id
        WHERE cc.contract_address IS NOT NULL
          AND cc.contract_address != ''
    """)
    for chain, sym, addr in cur.fetchall():
        if sym and addr and '0x' in addr:
            chain_key = {
                'ethereum': 'Ethereum',
                'arbitrum': 'Arbitrum',
                'base': 'Base',
                'bsc': 'BNB',
            }.get(chain, chain.capitalize())
            symbol_map[chain_key][sym] = addr

    # 2. Get V4 pools missing pool_id, grouped by network
    cur.execute("""
        SELECT lp.id, UPPER(c0.symbol) as s0, UPPER(c1.symbol) as s1,
               lp.fee_tier, lp.network
        FROM liquidity_pool lp
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE lp.protocol = 'Uniswap V4'
          AND lp.pool_id IS NULL
        ORDER BY lp.network
    """)
    all_missing = cur.fetchall()
    logging.info(f"Found {len(all_missing)} V4 pools without pool_id")

    if not all_missing:
        logging.info("No V4 pools need pool_id — nothing to do.")
        cur.close()
        conn.close()
        return

    # Group by network and filter to supported ones
    missing_by_network = defaultdict(list)
    for row in all_missing:
        network = row[4]
        if network in SUPPORTED_NETWORKS:
            missing_by_network[network].append(row)

    for net in missing_by_network:
        logging.info(f"  {net}: {len(missing_by_network[net])} pools to backfill")

    unsupported = sum(1 for r in all_missing if r[4] not in SUPPORTED_NETWORKS)
    if unsupported:
        logging.info(f"  Skipping {unsupported} pools on unsupported networks (BNB)")

    # 3. For each supported network, fetch ALL pools from subgraph, match, UPDATE
    total_updated = 0
    total_skipped = 0

    for network, missing in missing_by_network.items():
        logging.info(f"\n{'='*60}")
        logging.info(f"Processing {network}: {len(missing)} pools")

        # Build address→symbol reverse map for this network
        addr_to_symbol = {v: k for k, v in symbol_map.get(network, {}).items()}

        # Fetch all V4 pools from subgraph
        fetcher = UniswapV4Fetcher(verbose=False, network=network)
        try:
            all_pools = fetch_all_v4_pools(fetcher)
        except Exception as e:
            logging.error(f"  Failed to fetch pools from {network} subgraph: {e}")
            total_skipped += len(missing)
            continue

        if not all_pools:
            logging.warning(f"  No pools returned from {network} subgraph — skipping")
            total_skipped += len(missing)
            continue

        # Build local lookup: (addr0_sorted, addr1_sorted, fee_bips) → pool_id
        # The subgraph token IDs are hex addresses.
        pool_lookup = {}
        for p in all_pools:
            t0 = p['token0'].lower()
            t1 = p['token1'].lower()
            key = (t0, t1, p['feeTier'])
            pool_lookup[key] = p['id']
            # Also sorted direction
            if t0 != t1:
                sorted_key = tuple(sorted([t0, t1])) + (p['feeTier'],)
                if sorted_key not in pool_lookup:
                    pool_lookup[sorted_key] = p['id']

        # Match DB pools to subgraph pools
        for pool_db_id, c0, c1, fee, _net in missing:
            addr0 = symbol_map.get(network, {}).get(c0)
            addr1 = symbol_map.get(network, {}).get(c1)

            if not addr0 or not addr1:
                logging.warning(f"  Skipping pool {pool_db_id} ({c0}-{c1}): address not found")
                total_skipped += 1
                continue

            fee_bips = normalize_fee_to_bips(fee)
            try:
                fee_int = int(fee_bips)
            except ValueError:
                total_skipped += 1
                continue

            # Look up by (addr0, addr1, fee) in both directions
            key = (addr0.lower(), addr1.lower(), str(fee_int))
            pool_id = pool_lookup.get(key)
            if not pool_id:
                key_r = (addr1.lower(), addr0.lower(), str(fee_int))
                pool_id = pool_lookup.get(key_r)
            if not pool_id:
                total_skipped += 1
                continue

            cur.execute(
                "UPDATE liquidity_pool SET pool_id = %s WHERE id = %s",
                (pool_id, pool_db_id)
            )
            total_updated += 1

            if total_updated % 100 == 0:
                conn.commit()
                logging.info(f"  Progress: {total_updated} updated, {total_skipped} skipped")

        conn.commit()
        logging.info(f"  {network} done: {len(missing) - (total_skipped - sum(len(v) for v in missing_by_network.values() if v == missing))} in this batch")

    cur.close()
    conn.close()
    logging.info(f"\n{'='*60}")
    logging.info(f"Backfill complete: {total_updated} updated, {total_skipped} skipped")


if __name__ == '__main__':
    main()