import psycopg2
conn = psycopg2.connect("host=postgres port=5432 dbname=chaintelligence user=airflow password=airflow")
c = conn.cursor()

# Check every swaps partition for pool 609
swap_tables = [
    'swaps', 'swaps_2025_06', 'swaps_2025_07', 'swaps_2025_08',
    'swaps_2025_09', 'swaps_2025_10', 'swaps_2025_11', 'swaps_2025_12',
    'swaps_2026_01', 'swaps_2026_02', 'swaps_2026_03', 'swaps_2026_04',
    'swaps_2026_05', 'swaps_2026_06', 'swaps_2026_07', 'swaps_2026_08',
    'swaps_default'
]

total = 0
for tbl in swap_tables:
    c.execute(f"SELECT COUNT(*), COALESCE(MIN(ts)::text, 'null'), COALESCE(MAX(ts)::text, 'null'), COALESCE(SUM(amount_usd)::text, 'null') FROM {tbl} WHERE pool_id = 609")
    cnt, mn, mx, vol = c.fetchone()
    if cnt > 0:
        total += cnt
        print(f"{tbl}: count={cnt}, from={mn}, to={mx}, vol={vol}")

print(f"\nTotal swaps for pool 609: {total}")

# Also check the tx_hashes in the main swaps table
if total > 0:
    for tbl in swap_tables:
        c.execute(f"SELECT tx_hash, amount_usd, ts FROM {tbl} WHERE pool_id = 609 ORDER BY ts DESC LIMIT 5")
        rows = c.fetchall()
        if rows:
            print(f"\nSample from {tbl}:")
            for r in rows:
                print(f"  tx={r[0][:20]}... amount_usd={r[1]} ts={r[2]}")
            break
