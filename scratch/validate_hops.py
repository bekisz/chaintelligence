"""Scratch: find and reconstruct multi-hop txs to validate hops counting."""
import psycopg2
DSN = "host=postgres port=5432 dbname=chaintelligence user=airflow password=airflow"

def canonical_pair(s0, s1, ad0, ad1):
    if ad0 and ad1 and ad0 != ad1:
        return (s0, s1) if ad0 < ad1 else (s1, s0)
    return s0, s1

def fetch_legs(conn, tx_hashes):
    cur = conn.cursor()
    out = {}
    for tx in tx_hashes:
        cur.execute("""
            SELECT s.log_index, s.pool_id,
                   (SELECT symbol FROM coin WHERE coin_id=lp.coin0_id),
                   (SELECT symbol FROM coin WHERE coin_id=lp.coin1_id),
                   cc0.contract_address, cc1.contract_address,
                   s.amount0, s.amount1
            FROM swaps s JOIN liquidity_pool lp ON s.pool_id=lp.id
            LEFT JOIN coin_contract cc0 ON cc0.coin_id=lp.coin0_id AND cc0.chain_id=lp.chain_id
            LEFT JOIN coin_contract cc1 ON cc1.coin_id=lp.coin1_id AND cc1.chain_id=lp.chain_id
            WHERE s.tx_hash=%s ORDER BY s.log_index
        """, (tx,))
        legs = []
        for (lg,pid,s0,s1,ad0,ad1,a0,a1) in cur.fetchall():
            t0,t1 = canonical_pair((s0 or '').upper(),(s1 or '').upper(),ad0,ad1)
            inp = t0 if (a0 or 0)>0 else (t1 if (a1 or 0)>0 else None)
            legs.append({'t0':t0,'t1':t1,'inp':inp})
        out[tx]=legs
    return out

def route_hops(legs, start_token):
    """Follow the swap chain from start_token; return number of legs in the
    contiguous route until the next leg can't continue (or end reached)."""
    current = start_token
    hops = 0
    remaining = [l for l in legs if l['inp'] is not None]
    while True:
        nxt = [l for l in remaining if l['inp'] == current]
        if not nxt:
            break
        out = nxt[0]['t0'] if nxt[0]['inp']==nxt[0]['t0'] else nxt[0]['t1']
        hops += 1
        current = out
        remaining = [l for l in remaining if l != nxt[0]]
    return hops, current

conn = psycopg2.connect(DSN)
# find a tx that chains through != tokens (multi-hop): pair A-B and B-C present
cur = conn.cursor()
cur.execute("""
  SELECT s.tx_hash, count(distinct (LEAST(cs0.symbol, cs1.symbol)||'/'||GREATEST(cs0.symbol, cs1.symbol))) AS n_pairs
  FROM swaps s
  JOIN liquidity_pool lp ON s.pool_id=lp.id
  JOIN coin cs0 ON cs0.coin_id=lp.coin0_id JOIN coin cs1 ON cs1.coin_id=lp.coin1_id
  WHERE s.ts>now()-interval '5 days' AND s.amount_usd>50000
  GROUP BY s.tx_hash HAVING count(distinct (LEAST(cs0.symbol, cs1.symbol)||'/'||GREATEST(cs0.symbol, cs1.symbol))) >= 4
  ORDER BY count(distinct (LEAST(cs0.symbol, cs1.symbol)||'/'||GREATEST(cs0.symbol, cs1.symbol))) DESC LIMIT 1
""")
row = cur.fetchone()
print("candidate tx:", row[0])
tx = row[0]
legs = fetch_legs(conn, [tx])[tx]
seen = set()
print("LEGS:")
for l in sorted(legs, key=lambda x: legs.index(x)):
    pass
for i,l in enumerate(legs):
    print(f"  {i}: {l['t0']}/{l['t1']} inp={l['inp']}")
# For query WETH->USDC: start=WETH
start_token = "WETH"
hops, final = route_hops(legs, start_token)
print(f"route from {start_token}: hops={hops}, ends at {final}")
conn.close()