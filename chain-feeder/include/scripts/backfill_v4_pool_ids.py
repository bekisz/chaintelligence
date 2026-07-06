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
    Returns list of {id, token0, token1, sym0, sym1, feeTier, hooks, liquidity}.
    hooks is included to disambiguate pools with same tokens+fee (prefer no-hook pools).
    """
    all_pools = []
    skip = 0
    page_size = 1000
    max_pools = 200000  # safety limit

    while skip < max_pools:
        query = f"""
        {{
          pools(first: {page_size}, skip: {skip}) {{
            id
            token0 {{ id symbol }}
            token1 {{ id symbol }}
            feeTier
            hooks
            liquidity
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
                'sym0': p['token0'].get('symbol', ''),
                'sym1': p['token1'].get('symbol', ''),
                'feeTier': p['feeTier'],
                'hooks': p.get('hooks', ''),
                'liquidity': int(p.get('liquidity', '0') or '0'),
            })

        logging.info(f"  Fetched {len(all_pools)} pools so far...")
        if len(pools) < page_size:
            break
        skip += page_size
        time.sleep(0.3)  # rate limit buffer

    logging.info(f"  Total pools fetched: {len(all_pools)}")
    return all_pools


def _try_match(pool_lookup: dict, sym0: str, sym1: str, fee: str):
    """Try to find a pool_id in pool_lookup by (sym0, sym1, fee) in both orderings."""
    key = (sym0, sym1, fee)
    pool_id = pool_lookup.get(key)
    if not pool_id and sym0 != sym1:
        key_r = (sym1, sym0, fee)
        pool_id = pool_lookup.get(key_r)
    return pool_id


def main():
    logging.info("Connecting to DB...")
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    except Exception as e:
        logging.error(f"Failed to connect: {e}")
        sys.exit(1)
    cur = conn.cursor()

    # 1. Get V4 pools missing pool_id, grouped by network
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

    # 2. For each supported network, fetch ALL pools from subgraph with token symbols,
    #    then match by (symbol0, symbol1, fee) directly — no address lookup needed.
    total_updated = 0
    total_skipped = 0

    for network, missing in missing_by_network.items():
        logging.info(f"\n{'='*60}")
        logging.info(f"Processing {network}: {len(missing)} pools")

        # Fetch all V4 pools from subgraph (with token symbols)
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

        # Build lookup: (symbol0_upper, symbol1_upper, fee) → pool_id (bytes32)
        # When multiple pools share the same tokens+fee (different hooks), prefer:
        # 1. Pools WITHOUT hooks (0x000...000) — standard V4 pools (no hook contract)
        # 2. Pools with higher liquidity
        # Normalize both DB and subgraph side: uppercase, 8-char truncation
        pool_lookup = {}
        # Track best pool per key to compare when duplicates arise
        best_pool = {}
        for p in all_pools:
            s0 = p.get('sym0', '').upper()[:8]
            s1 = p.get('sym1', '').upper()[:8]
            fee = str(p['feeTier'])
            if not s0 or not s1:
                continue

            def pool_score(pp):
                """Higher score = better pool to link to. Prefer no-hook, high-liquidity."""
                no_hook = pp.get('hooks', '') in ('0x0000000000000000000000000000000000000000', '0x', '')
                liq = pp.get('liquidity', 0) or 0
                return (2 if no_hook else 0) + min(liq, 10**30)

            keys = [(s0, s1, fee)]
            if s0 != s1:
                keys.append(tuple(sorted([s0, s1])) + (fee,))
            for key in keys:
                existing = best_pool.get(key)
                if existing is None or pool_score(p) > pool_score(existing):
                    best_pool[key] = p
                    pool_lookup[key] = p['id']

        # Match DB pools to subgraph pools by symbol+symbol+fee
        net_updated = 0
        for pool_db_id, c0, c1, fee, _net in missing:
            c0n = c0.upper()[:8]
            c1n = c1.upper()[:8]

            # Determine fee tier type and try multiple match strategies
            STANDARD_TIERS_PCT = {'0.01%', '0.05%', '0.08%', '0.3%', '1.0%'}

            fee = fee if fee else ''

            # Strategy 1: Try exact fee match (standard tiers already match this)
            fee_clean = fee.strip().replace('%', '').strip()
            pool_id = _try_match(pool_lookup, c0n, c1n, fee_clean)

            # Strategy 2: If fee is a %, compute bips and try matching
            if not pool_id and '%' in fee:
                try:
                    pct = float(fee_clean)
                    fee_bips = str(int(pct * 10000))
                    pool_id = _try_match(pool_lookup, c0n, c1n, fee_bips)
                except ValueError:
                    pass

            # Strategy 3: If fee starts with '%' and is high (>50%), it's dynamic — match feeTier=0
            if not pool_id and fee in STANDARD_TIERS_PCT:
                # Standard tier that didn't match — just skip
                pass
            elif not pool_id:
                # Try dynamic fee match (feeTier=0 or 8388608 in subgraph)
                pool_id = _try_match(pool_lookup, c0n, c1n, '0')
                if not pool_id:
                    pool_id = _try_match(pool_lookup, c0n, c1n, '8388608')

            if not pool_id:
                total_skipped += 1
                continue

            cur.execute(
                "UPDATE liquidity_pool SET pool_id = %s WHERE id = %s",
                (pool_id, pool_db_id)
            )
            total_updated += 1
            net_updated += 1

            if total_updated % 200 == 0:
                conn.commit()
                logging.info(f"  Progress: {total_updated} updated, {total_skipped} skipped")

        conn.commit()
        logging.info(f"  {network} done: matched {net_updated} of {len(missing)}")

    cur.close()
    conn.close()
    logging.info(f"\n{'='*60}")
    logging.info(f"Backfill complete: {total_updated} updated, {total_skipped} skipped")


if __name__ == '__main__':
    main()