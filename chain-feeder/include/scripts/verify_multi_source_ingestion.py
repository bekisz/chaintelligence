#!/usr/bin/env python3
import os
import sys
import psycopg2

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'chain-feeder', 'include'))

from contract_ingestion import MultiSourceContractEngine, SOURCE_CONFIDENCE

def main():
    conn_str = "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433"
    try:
        conn = psycopg2.connect(conn_str)
    except Exception:
        db_conn = os.getenv("DATA_WAREHOUSE_DB") or os.getenv("DB_CONN")
        conn = psycopg2.connect(db_conn)

    print("Running MultiSourceContractEngine fallback resolution...")
    engine = MultiSourceContractEngine()
    resolved = engine.resolve_missing_contracts(conn, min_liquidity_usd=100.0)
    print(f"Fallback resolution complete. Processed {resolved} missing coins.")

    cur = conn.cursor()

    # Query newly resolved coins in coin_contract
    cur.execute("""
        SELECT c.symbol, c.name, ch.name AS chain, cc.contract_address, cc.source, cc.confidence_score
        FROM coin_contract cc
        JOIN coin c ON cc.coin_id = c.coin_id
        JOIN chain ch ON cc.chain_id = ch.id
        WHERE cc.source IN ('dexscreener', 'coingecko')
        ORDER BY c.symbol, ch.name
    """)
    rows = cur.fetchall()

    print(f"\nContracts in DB resolved via Multi-Source Fallbacks ({len(rows)} total):")
    print(f"{'Symbol':<12} | {'Chain':<12} | {'Source':<12} | {'Confidence':<10} | {'Contract Address'}")
    print("-" * 90)
    for sym, name, chain, addr, source, conf in rows:
        print(f"{sym:<12} | {chain:<12} | {source:<12} | {conf:<10} | {addr}")

    # Test Conflict Resolution: Try to overwrite a 'cmc' contract (confidence 90) with a 'dexscreener' contract (confidence 70)
    cur.execute("SELECT coin_id, chain_id, contract_address, source, confidence_score FROM coin_contract WHERE source = 'cmc' LIMIT 1")
    test_row = cur.fetchone()
    if test_row:
        t_coin, t_chain, orig_addr, orig_source, orig_conf = test_row
        fake_addr = "0x0000000000000000000000000000000000000999"

        # Attempt overwrite with dexscreener (70 < 90)
        engine.upsert_contract(cur, t_coin, t_chain, fake_addr, decimals=18, is_native=False, source='dexscreener')
        conn.commit()

        # Check address in DB
        cur.execute("SELECT contract_address, source FROM coin_contract WHERE coin_id = %s AND chain_id = %s", (t_coin, t_chain))
        post_addr, post_source = cur.fetchone()

        print("\nConflict Resolution Test:")
        print(f"  Original ({orig_source}, confidence {orig_conf}): {orig_addr}")
        print(f"  Attempted overwrite with dexscreener (confidence 70): {fake_addr}")
        print(f"  Result in DB: {post_addr} (Source: {post_source})")
        assert post_addr == orig_addr, "CONFLICT TEST FAILED: Lower priority source overwrote higher priority contract!"
        print("  SUCCESS: Conflict resolution rule successfully blocked lower priority overwrite!")

    cur.close()
    conn.close()

if __name__ == '__main__':
    main()
