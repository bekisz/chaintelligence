#!/usr/bin/env python3
"""
Backfill script to populate fallback pool_id for any pool missing both pool_id and pool_address,
and apply the database CHECK constraint: CHECK (pool_id IS NOT NULL OR pool_address IS NOT NULL).
"""

import os
import sys
import logging
import psycopg2
from eth_hash.auto import keccak

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'routing'))

try:
    from config import DATA_WAREHOUSE_DB
except ImportError:
    DATA_WAREHOUSE_DB = "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433"


_V4_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}


def _derive_v4_pool_id(c0_hex: str, c1_hex: str, fee: int, tick_spacing: int) -> str:
    a = bytes.fromhex(c0_hex.lower().removeprefix('0x').rjust(40, '0'))
    b = bytes.fromhex(c1_hex.lower().removeprefix('0x').rjust(40, '0'))
    if b < a:
        a, b = b, a
    hooks = b'\x00' * 32
    enc = (a.rjust(32, b'\x00') + b.rjust(32, b'\x00') +
           fee.to_bytes(32, 'big') + tick_spacing.to_bytes(32, 'big', signed=True) + hooks)
    return '0x' + keccak(enc).hex()


def main():
    logging.info("Connecting to Database...")
    try:
        conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    except Exception as e:
        conn = psycopg2.connect("dbname=chaintelligence user=airflow password=airflow host=localhost port=5433")

    cur = conn.cursor()

    # Load contract addresses map: (coin_id, chain_id) -> contract_address
    cur.execute("""
        SELECT coin_id, chain_id, contract_address 
        FROM coin_contract
        WHERE contract_address ~ '^0x[0-9a-fA-F]{40}$'
    """)
    token_addr_map = {}
    for coin_id, chain_id, addr in cur.fetchall():
        token_addr_map[(coin_id, chain_id)] = addr.lower()

    # Query pools where both pool_id and pool_address are NULL
    cur.execute("""
        SELECT lp.id, lp.coin0_id, lp.coin1_id, lp.chain_id, lp.fee_bps
        FROM liquidity_pool lp
        WHERE lp.pool_id IS NULL AND lp.pool_address IS NULL
    """)
    missing_pools = cur.fetchall()
    logging.info(f"Found {len(missing_pools)} pools requiring identifier backfill.")

    updated_count = 0
    for pool_db_id, coin0_id, coin1_id, chain_id, fee_bps in missing_pools:
        addr0 = token_addr_map.get((coin0_id, chain_id))
        addr1 = token_addr_map.get((coin1_id, chain_id))

        if addr0 and addr1:
            raw_fee = int(round(float(fee_bps))) if fee_bps is not None else 100
            tick_spacing = _V4_TICK_SPACING.get(raw_fee, 10)
            derived_id = _derive_v4_pool_id(addr0, addr1, raw_fee, tick_spacing)
        else:
            # Deterministic fallback hash if contract addresses are not in coin_contract table
            seed_str = f"v4-fallback-{pool_db_id}-{coin0_id}-{coin1_id}-{fee_bps}"
            derived_id = "0x" + keccak(seed_str.encode('utf-8')).hex()

        cur.execute("""
            UPDATE liquidity_pool
            SET pool_id = %s
            WHERE id = %s
        """, (derived_id, pool_db_id))
        updated_count += 1

    conn.commit()
    logging.info(f"Successfully backfilled pool_id for {updated_count} pools.")

    # Check if constraint already exists
    cur.execute("""
        SELECT constraint_name 
        FROM information_schema.table_constraints 
        WHERE table_name = 'liquidity_pool' AND constraint_name = 'chk_pool_has_identifier'
    """)
    if not cur.fetchone():
        logging.info("Applying DB CHECK constraint `chk_pool_has_identifier`...")
        cur.execute("""
            ALTER TABLE liquidity_pool 
            ADD CONSTRAINT chk_pool_has_identifier 
            CHECK (pool_id IS NOT NULL OR pool_address IS NOT NULL)
        """)
        conn.commit()
        logging.info("DB CHECK constraint `chk_pool_has_identifier` successfully applied!")
    else:
        logging.info("DB CHECK constraint `chk_pool_has_identifier` already exists.")

    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
