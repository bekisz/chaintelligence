import psycopg2
conn = psycopg2.connect("host=postgres port=5432 dbname=chaintelligence user=airflow password=airflow")
c = conn.cursor()

# Check pool 609
c.execute("""
    SELECT lp.id, lp.pool_id, lp.pool_address, p.name, c.name,
           c0.symbol, c1.symbol, lp.fee_bps
    FROM liquidity_pool lp
    JOIN protocol p ON lp.protocol_id = p.id
    JOIN chain c ON lp.chain_id = c.id
    JOIN coin c0 ON lp.coin0_id = c0.coin_id
    JOIN coin c1 ON lp.coin1_id = c1.coin_id
    WHERE lp.id = 609
""")
r = c.fetchone()
print("Pool 609:", r)

# Check if there are other V4 pools with this same pair on Ethereum
pool_id_to_check = "0x42264d4cb5dc654b4289986574580c86e4cd46e5cee26514ef509453e28eb1cb"
c.execute("""
    SELECT lp.id, lp.pool_id, p.name, c.name, c0.symbol, c1.symbol
    FROM liquidity_pool lp
    JOIN protocol p ON lp.protocol_id = p.id
    JOIN chain c ON lp.chain_id = c.id
    JOIN coin c0 ON lp.coin0_id = c0.coin_id
    JOIN coin c1 ON lp.coin1_id = c1.coin_id
    WHERE lp.pool_id = %s
""", (pool_id_to_check,))
for r in c.fetchall():
    print("Same pool_id found:", r)

# Check a few V4 pools to test
c.execute("""
    SELECT lp.id, lp.pool_id, p.name, c.name, c0.symbol, c1.symbol
    FROM liquidity_pool lp
    JOIN protocol p ON lp.protocol_id = p.id
    JOIN chain c ON lp.chain_id = c.id
    JOIN coin c0 ON lp.coin0_id = c0.coin_id
    JOIN coin c1 ON lp.coin1_id = c1.coin_id
    WHERE p.name ILIKE '%v4%'
    LIMIT 5
""")
print("\nFirst 5 V4 pools:")
for r in c.fetchall():
    print(r)
