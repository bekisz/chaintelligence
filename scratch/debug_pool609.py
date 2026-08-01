import psycopg2
conn = psycopg2.connect("host=postgres port=5432 dbname=chaintelligence user=airflow password=airflow")
c = conn.cursor()

# Check pool 609 creation time and pool_address (for V4, pool_id IS the identifier)
c.execute("""
    SELECT id, pool_name, created_at, pool_address, pool_id, coin0_id, coin1_id, fee_bps, chain_id, protocol_id
    FROM liquidity_pool WHERE id = 609
""")
r = c.fetchone()
print("Pool 609:")
print(f"  id={r[0]} name={r[1]} created={r[2]}")
print(f"  pool_address={r[3]}")
print(f"  pool_id(v4)={r[4]}")
print(f"  coin0_id={r[5]} coin1_id={r[6]}")
print(f"  fee_bps={r[7]} chain_id={r[8]} protocol_id={r[9]}")

# Check the swaps_2026_06 table for pool 609 - what pool_id field?
c.execute("""
    SELECT tx_hash, amount_usd, ts FROM swaps WHERE pool_id = 609 ORDER BY ts
""")
print(f"\nSwaps for pool 609:")
for r in c.fetchall():
    print(f"  tx={r[0][:30]}... amt={r[1]:.2f} ts={r[2]}")

# Check if swaps for these tx hashes have a pool_id that matches the actual on-chain poolId
# Let's look at the first swap's full data
c.execute("""
    SELECT * FROM swaps WHERE tx_hash = '\\xba0f69f7c914e15e1c638334a10b10685a4e94534b52a3bc9cee86bbe8914de7'
""")
for r in c.fetchall():
    print(f"\nFull swap row: {r}")
