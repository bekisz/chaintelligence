#!/usr/bin/env python3
"""
One-shot backfill: replace synthetic `v4-<t0>-<t1>` pool_address values on
Uniswap V4 (and PancakeSwap V4) liquidity_pool rows with the real on-chain
bytes32 poolId, so the frontend can link to the Uniswap/PancakeSwap explorer.

Method: for each V4 pool, take any position in it, re-run
verify_v4_position_rpc(token_id) to get the authoritative PoolManager pool
key (currency0, currency1, fee, tickSpacing, hooks), then derive the poolId
via the shared keccak formula. This handles native-ETH and non-standard
fee/tickSpacing pools that deterministic inference cannot.

For closed positions where RPC returns no pool key, fall back to deriving
from the pool's coin contract addresses + fee_bps (standard tiers only).

Usage:
    set -a; source .env.secrets; set +a
    export DATA_WAREHOUSE_DB="host=localhost port=5433 dbname=chaintelligence user=airflow password=airflow"
    python chain-feeder/include/scripts/backfill_v4_pool_addresses.py
"""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder'))      # for `include.*`
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'routing'))  # for config

import psycopg2  # noqa: E402
from config import DATA_WAREHOUSE_DB  # noqa: E402
from include.v4_pool import derive_v4_pool_id  # noqa: E402
from include.graph_discovery_client import verify_v4_position_rpc  # noqa: E402

# fee_bps (bips) -> V4 fee units (Uniswap: 1% = 10000). fee_bps is in bips
# (1% = 100 bips), so multiply by 100. Only standard tiers have a known
# tickSpacing; non-standard ones are skipped in the fallback path.
_FEE_BIPS_TO_V4FEE = lambda bps: int(round(float(bps) * 100)) if bps is not None else None
_V4_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}


def _coin_contract_address(cur, coin_id, chain_id):
    """Resolve a coin's contract address for a specific chain (tracked preferred)."""
    cur.execute("""
        SELECT contract_address FROM coin_contract
        WHERE coin_id = %s AND chain_id = %s AND contract_address ~ '^0x[0-9a-fA-F]{40}$'
          AND tracked = true
        ORDER BY tracked DESC
        LIMIT 1
    """, (coin_id, chain_id))
    row = cur.fetchone()
    if row:
        return row[0]
    # fallback: any 0x40 address for this coin/chain
    cur.execute("""
        SELECT contract_address FROM coin_contract
        WHERE coin_id = %s AND chain_id = %s AND contract_address ~ '^0x[0-9a-fA-F]{40}$'
        LIMIT 1
    """, (coin_id, chain_id))
    row = cur.fetchone()
    return row[0] if row else None


def main():
    logging.info("Connecting to DB...")
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()

    # V4 pools still carrying the synthetic composite, with one representative
    # position (token_id + network) to re-verify the pool key via RPC.
    cur.execute("""
        SELECT DISTINCT ON (lp.id)
            lp.id, lp.fee_bps, lp.coin0_id, lp.coin1_id, lp.chain_id,
            ch.name AS network, lpp.token_id
        FROM liquidity_pool lp
        JOIN chain ch ON lp.chain_id = ch.id
        JOIN protocol pr ON lp.protocol_id = pr.id
        LEFT JOIN liquidity_pool_position lpp ON lpp.pool_id = lp.id
        WHERE pr.name ILIKE '%V4%'
          AND lp.pool_address LIKE 'v4-%'
        ORDER BY lp.id, lpp.token_id
    """)
    rows = cur.fetchall()
    logging.info(f"Found {len(rows)} V4 pools with synthetic pool_address to backfill")

    updated = 0
    skipped = 0
    for pool_id, fee_bps, c0_id, c1_id, chain_id, network, token_id in rows:
        pool_id_bytes = None

        # Primary: authoritative RPC re-verification of the pool key.
        if token_id is not None:
            try:
                _, _, pkey, _ = verify_v4_position_rpc(token_id, network=network)
                if pkey and pkey.get('token0') and pkey.get('token1'):
                    pool_id_bytes = derive_v4_pool_id(
                        pkey['token0'], pkey['token1'],
                        pkey['fee'], pkey['tickSpacing'], pkey.get('hooks'))
            except Exception as e:
                logging.warning(f"  pool {pool_id}: RPC verify failed: {e}")

        # Fallback: deterministic derivation from coin addresses + fee_bps
        # (standard tiers only; non-standard tickSpacing is unknowable here).
        if not pool_id_bytes and fee_bps is not None:
            v4fee = _FEE_BIPS_TO_V4FEE(fee_bps)
            ts = _V4_TICK_SPACING.get(v4fee) if v4fee else None
            if v4fee and ts:
                try:
                    t0 = _coin_contract_address(cur, c0_id, chain_id)
                    t1 = _coin_contract_address(cur, c1_id, chain_id)
                    if t0 and t1:
                        pool_id_bytes = derive_v4_pool_id(t0, t1, v4fee, ts)
                except Exception as e:
                    logging.warning(f"  pool {pool_id}: fallback derive failed: {e}")

        if not pool_id_bytes:
            logging.warning(f"  pool {pool_id} (token {token_id}, {network}): could not derive — leaving synthetic")
            skipped += 1
            continue

        cur.execute(
            "UPDATE liquidity_pool SET pool_address = %s WHERE id = %s",
            (pool_id_bytes, pool_id))
        updated += 1
        logging.info(f"  pool {pool_id} ({network}): {pool_id_bytes}")

    conn.commit()
    cur.close()
    conn.close()
    logging.info(f"Done: {updated} updated, {skipped} skipped")


if __name__ == '__main__':
    main()
