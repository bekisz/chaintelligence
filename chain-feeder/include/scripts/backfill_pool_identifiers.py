#!/usr/bin/env python3
"""
One-shot backfill script to populate pool_address and pool_id in the liquidity_pool table.
Uses fast targeted batch GraphQL queries for subgraph pools and offline CREATE2 derivation for V2/V3.
"""

import os
import sys
import logging
import time
import yaml
import psycopg2
import requests
from eth_hash.auto import keccak
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'dags'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'routing'))

from config import DATA_WAREHOUSE_DB

# Subgraph ID mapping (copied from uniswap_utils.py)
SUBGRAPH_IDS = {
    ('Base', 'Aerodrome'): 'GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM',
    ('Ethereum', 'Uniswap V4'): 'DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G',
    ('Arbitrum', 'Uniswap V4'): 'G5TsTKNi8yhPSV7kycaE23oWbqv9zzNqR49FoEQjzq1r',
    ('Base', 'Uniswap V4'): 'FUbEPQw1oMghy39fwWBFY5fE6MXPXZQtjncQy2cXdrNS',
    ('BNB', 'PancakeSwap V4'): '7XgdLW3bts4HktCYsu9dy8bEnuiNeZuftcuK3Aj4JXYV',
}


def to_checksum_address(address: str) -> str:
    addr_lower = address.lower().replace('0x', '')
    if len(addr_lower) != 40:
        return address
    address_hash = keccak(addr_lower.encode('ascii')).hex()
    checksum_address = '0x' + ''.join(
        c.upper() if int(address_hash[i], 16) >= 8 else c 
        for i, c in enumerate(addr_lower)
    )
    return checksum_address


def _derive_address(t0_bytes: bytes, t1_bytes: bytes, fee_val: int, factory_hex: str, init_hash_hex: str, is_v2: bool = False) -> str:
    if is_v2:
        salt = keccak(t0_bytes + t1_bytes)
    else:
        salt = keccak(b'\x00' * 12 + t0_bytes + b'\x00' * 12 + t1_bytes + fee_val.to_bytes(32, 'big'))
    f_bytes = bytes.fromhex(factory_hex.removeprefix('0x'))
    ih_bytes = bytes.fromhex(init_hash_hex.removeprefix('0x'))
    derived = '0x' + keccak(b'\xff' + f_bytes + salt + ih_bytes)[12:].hex()
    return to_checksum_address(derived)


def _derive_v4_pool_id(c0_hex: str, c1_hex: str, fee: int, tick_spacing: int) -> str:
    a = bytes.fromhex(c0_hex.lower().removeprefix('0x').rjust(40, '0'))
    b = bytes.fromhex(c1_hex.lower().removeprefix('0x').rjust(40, '0'))
    if b < a:
        a, b = b, a
    hooks = b'\x00' * 32
    enc = (a.rjust(32, b'\x00') + b.rjust(32, b'\x00') +
           fee.to_bytes(32, 'big') + tick_spacing.to_bytes(32, 'big', signed=True) + hooks)
    return '0x' + keccak(enc).hex()


def fetch_pools_batched(network: str, protocol: str, pool_params: list, is_v4: bool = False) -> dict:
    subgraph_id = SUBGRAPH_IDS.get((network, protocol))
    if not subgraph_id:
        return {}

    GRAPH_API_KEY = os.getenv('GRAPH_API_KEY', '')
    if not GRAPH_API_KEY or GRAPH_API_KEY == 'YOUR_GRAPH_API_KEY':
        url = f'https://gateway-arbitrum.network.thegraph.com/api/[api-key]/subgraphs/id/{subgraph_id}'
    else:
        url = f'https://gateway-arbitrum.network.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{subgraph_id}'

    results = {}
    page_size = 100
    logging.info(f"Targeted batch querying {len(pool_params)} pools from {network} {protocol} subgraph...")

    for i in range(0, len(pool_params), page_size):
        batch = pool_params[i:i+page_size]
        queries = []
        for idx, p in enumerate(batch):
            t0, t1 = sorted([p['t0'].lower(), p['t1'].lower()])
            # Align fee format
            fee_val = p['fee'] if p['fee'] is not None else 0
            if is_v4:
                queries.append(f"""
                  pool_{idx}: pools(where: {{ token0: "{t0}", token1: "{t1}", feeTier: "{fee_val}" }}) {{
                    id
                    hooks
                    liquidity
                  }}
                """)
            else:
                queries.append(f"""
                  pool_{idx}: pools(where: {{ token0: "{t0}", token1: "{t1}", feeTier: "{fee_val}" }}) {{
                    id
                    liquidity
                  }}
                """)

        query = "{\n" + "\n".join(queries) + "\n}"
        try:
            resp = requests.post(url, json={'query': query}, timeout=30.0)
            if resp.status_code == 200:
                data = resp.json().get('data', {}) or {}
                for idx, p in enumerate(batch):
                    pool_list = data.get(f"pool_{idx}", [])
                    if pool_list:
                        # Pick standard pool or highest liquidity
                        best_pool = None
                        best_score = -1
                        for sg_p in pool_list:
                            no_hook = sg_p.get('hooks', '') in ('0x0000000000000000000000000000000000000000', '0x', '', None)
                            liq = int(sg_p.get('liquidity', 0) or 0)
                            score = (2 if no_hook else 0) + min(liq, 10**18)
                            if score > best_score:
                                best_score = score
                                best_pool = sg_p
                        if best_pool:
                            results[p['db_id']] = (best_pool['id'], best_pool.get('hooks'))
            else:
                logging.error(f"Batch query failed: {resp.text}")
        except Exception as e:
            logging.error(f"Error querying batch: {e}")
        time.sleep(0.1)

    logging.info(f"Successfully matched {len(results)} pools for {network} {protocol}")
    return results


def main():
    # Load DEX config
    config_path = os.path.join(REPO_ROOT, 'config', 'dex_config.yaml')
    with open(config_path, 'r') as f:
        dex_config = yaml.safe_load(f)

    # Connect to DB
    logging.info("Connecting to DB...")
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()

    # Load contract addresses map
    cur.execute("""
        SELECT cc.coin_id, LOWER(ch.name) AS chain_name, cc.contract_address 
        FROM coin_contract cc
        JOIN chain ch ON cc.chain_id = ch.id
    """)
    token_addr_map = {}
    for r in cur.fetchall():
        token_addr_map[(r[0], r[1])] = r[2].lower()

    # Native to wrapped mappings for pools (CREATE2 uses ERC20 tokens)
    WRAPPED_MAP = {
        ('ethereum', '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'): '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
        ('arbitrum', '0x0000000000000000000000000000000000000000'): '0x82af49447d8a07e3bd95bd0d56f35241523fbab1',
        ('base', '0x0000000000000000000000000000000000000000'): '0x4200000000000000000000000000000000000006',
        ('bnb', '0xa05ccd2f8ac92afe092a7240e948aa3e17cef843'): '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',
    }

    # Load missing pools
    cur.execute("""
        SELECT lp.id, lp.pool_name, lp.chain_id, lp.protocol_id, lp.fee_bps, 
               ch.name AS network, pr.name AS protocol, lp.coin0_id, lp.coin1_id
        FROM liquidity_pool lp
        JOIN chain ch ON lp.chain_id = ch.id
        JOIN protocol pr ON lp.protocol_id = pr.id
        WHERE lp.pool_address IS NULL OR lp.pool_address = '' 
           OR lp.pool_id IS NULL OR lp.pool_id = ''
    """)
    pools_to_backfill = cur.fetchall()
    logging.info(f"Found {len(pools_to_backfill)} pools to backfill")

    if not pools_to_backfill:
        logging.info("All pools already have addresses and IDs populated.")
        return

    # Categorize pools: CREATE2 vs Subgraph batch querying
    create2_updates = []
    subgraph_by_group = defaultdict(list)
    skipped_count = 0

    for row in pools_to_backfill:
        lp_db_id, pool_name, chain_id, protocol_id, fee_bps, network, protocol, coin0_id, coin1_id = row
        db_chain_key = network.lower()
        config_chain_key = db_chain_key
        if config_chain_key == 'bnb':
            config_chain_key = 'bsc'

        # Resolve token addresses
        t0_addr = token_addr_map.get((coin0_id, db_chain_key))
        t1_addr = token_addr_map.get((coin1_id, db_chain_key))

        if t0_addr and (db_chain_key, t0_addr) in WRAPPED_MAP:
            t0_addr = WRAPPED_MAP[(db_chain_key, t0_addr)]
        if t1_addr and (db_chain_key, t1_addr) in WRAPPED_MAP:
            t1_addr = WRAPPED_MAP[(db_chain_key, t1_addr)]

        if not t0_addr or not t1_addr:
            skipped_count += 1
            continue

        # Validate that they are hex EVM addresses
        def is_valid_evm(addr):
            addr_clean = addr.lower().removeprefix('0x')
            if len(addr_clean) != 40:
                return False
            try:
                int(addr_clean, 16)
                return True
            except ValueError:
                return False

        if not is_valid_evm(t0_addr) or not is_valid_evm(t1_addr):
            skipped_count += 1
            continue

        if protocol in ('Uniswap V2', 'Uniswap V3', 'PancakeSwap V3'):
            # Offline derivation
            t0_addr_bytes = bytes.fromhex(t0_addr.removeprefix('0x'))
            t1_addr_bytes = bytes.fromhex(t1_addr.removeprefix('0x'))
            if t1_addr_bytes < t0_addr_bytes:
                t0_addr_bytes, t1_addr_bytes = t1_addr_bytes, t0_addr_bytes

            derived_addr = None
            if protocol == 'Uniswap V2':
                cfg = dex_config.get('uniswap_v2', {}).get(config_chain_key)
                if cfg:
                    derived_addr = _derive_address(t0_addr_bytes, t1_addr_bytes, 0, cfg['factory'], cfg['init_hash'], is_v2=True)
            else:
                proto_key = 'uniswap_v3' if protocol == 'Uniswap V3' else 'pancakeswap_v3'
                cfg = dex_config.get(proto_key, {}).get(config_chain_key)
                if cfg:
                    fee_val = int(fee_bps) if fee_bps is not None else 3000
                    derived_addr = _derive_address(t0_addr_bytes, t1_addr_bytes, fee_val, cfg['factory'], cfg['init_hash'], is_v2=False)

            if derived_addr:
                create2_updates.append((derived_addr, derived_addr, lp_db_id))
            else:
                skipped_count += 1
        elif protocol == 'Aerodrome' or 'V4' in protocol:
            # Targeted subgraph query
            fee_val = 0
            if fee_bps is not None:
                fee_val = int(round(float(fee_bps) * 100))
            
            raw_fee = int(round(float(fee_bps) * 100)) if fee_bps is not None else 100

            subgraph_by_group[(network, protocol)].append({
                'db_id': lp_db_id,
                't0': t0_addr,
                't1': t1_addr,
                'fee': fee_val,
                'raw_fee': raw_fee
            })

    # 1. Update CREATE2 pools
    logging.info(f"Applying offline CREATE2 derivation updates for {len(create2_updates)} pools...")
    for idx, (p_addr, p_id, lp_db_id) in enumerate(create2_updates):
        cur.execute("""
            UPDATE liquidity_pool 
            SET pool_address = %s, pool_id = %s
            WHERE id = %s
        """, (p_addr, p_id, lp_db_id))
        if idx > 0 and idx % 1000 == 0:
            conn.commit()

    conn.commit()
    logging.info("CREATE2 pool updates applied.")

    # 2. Update Subgraph pools
    subgraph_updates = 0
    for (network, protocol), pool_params in subgraph_by_group.items():
        is_v4 = "V4" in protocol
        matched_results = fetch_pools_batched(network, protocol, pool_params, is_v4=is_v4)

        for p in pool_params:
            lp_db_id = p['db_id']
            derived_addr = None
            derived_id = None

            if lp_db_id in matched_results:
                sg_id, sg_hooks = matched_results[lp_db_id]
                derived_id = sg_id
                # For V3/Aerodrome, ID is the pool address. For V4, pool_address is not standard.
                derived_addr = sg_id if not is_v4 else None
            elif is_v4:
                # V4 Fallback to hookless derivation
                raw_fee = p['raw_fee']
                _V4_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}
                tick_spacing = _V4_TICK_SPACING.get(raw_fee, 10)
                derived_id = _derive_v4_pool_id(p['t0'], p['t1'], raw_fee, tick_spacing)
                derived_addr = None

            if derived_addr or derived_id:
                cur.execute("""
                    UPDATE liquidity_pool 
                    SET pool_address = COALESCE(pool_address, %s),
                        pool_id = COALESCE(pool_id, %s)
                    WHERE id = %s
                """, (derived_addr, derived_id, lp_db_id))
                subgraph_updates += 1

        conn.commit()

    cur.close()
    conn.close()

    total_updated = len(create2_updates) + subgraph_updates
    logging.info(f"Backfill finished: {total_updated} pools updated, {skipped_count} pools skipped.")


if __name__ == '__main__':
    main()
