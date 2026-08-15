"""Diagnose the 137 WBTC+RLUSD txs: show each tx's full leg sequence (in->out)."""
import psycopg2
conn = psycopg2.connect("host=postgres port=5432 dbname=chaintelligence user=airflow password=airflow")
cur = conn.cursor()
cur.execute("""
SELECT s.tx_hash
FROM swaps s
JOIN liquidity_pool lp ON s.pool_id = lp.id
JOIN coin c0 ON c0.coin_id = lp.coin0_id
JOIN coin c1 ON c1.coin_id = lp.coin1_id
WHERE s.ts >= '2026-04-01'
  AND (UPPER(c0.symbol)='WBTC' OR UPPER(c1.symbol)='WBTC')
GROUP BY s.tx_hash
HAVING EXISTS (
  SELECT 1 FROM swaps s2 JOIN liquidity_pool lp2 ON s2.pool_id = lp2.id
  JOIN coin c0 ON c0.coin_id = lp2.coin0_id
  JOIN coin c1 ON c1.coin_id = lp2.coin1_id
  WHERE s2.tx_hash = s.tx_hash AND (UPPER(c0.symbol)='RLUSD' OR UPPER(c1.symbol)='RLUSD'));
""")
txs = [r[0] for r in cur.fetchall()]
from collections import defaultdict, Counter
legs = defaultdict(list)
for i in range(0, len(txs), 1000):
    cur.execute("""
        SELECT s.tx_hash, s.log_index, UPPER(c0.symbol), UPPER(c1.symbol), s.amount0, s.amount1
        FROM swaps s JOIN liquidity_pool lp ON s.pool_id=lp.id
        JOIN coin c0 ON c0.coin_id=lp.coin0_id JOIN coin c1 ON c1.coin_id=lp.coin1_id
        WHERE s.tx_hash = ANY(%s) ORDER BY s.tx_hash, s.log_index
    """, (txs[i:i+1000],))
    for tx, lg, s0, s1, a0, a1 in cur.fetchall():
        legs[tx].append((s0, s1, a0, a1))

def in_tok(s0, s1, a0, a1):
    return s0 if (a0 or 0) > 0 else s1

# Summarize last_leg_out states: for each tx, list chains we can build
# starting from every WBTC leg and every RLUSD leg.
chain_counter = Counter()
for tx, ls in legs.items():
    # Try forward from WBTC and reverse from RLUSD
    for start_tok, want in (('WBTC','RLUSD'), ('RLUSD','WBTC')):
        # find legs with input == start_tok
        starts = [l for l in ls if in_tok(*l) == start_tok]
        if not starts:
            continue
        for start in starts:
            chain = [start]
            cur_t = [x for x in (start[0], start[1]) if x != in_tok(*start)][0]
            used = set()
            while cur_t != want:
                nxt = None
                for i2, l2 in enumerate(ls):
                    if i2 in used: continue
                    if in_tok(*l2) == cur_t:
                        nxt = (i2, l2); break
                if nxt is None: break
                used.add(nxt[0]); chain.append(nxt[1])
                cur_t = [x for x in (nxt[1][0], nxt[1][1]) if x != in_tok(*nxt[1])][0]
            if chain and (chain[-1][0]==want or chain[-1][1]==want):
                toks = [in_tok(*chain[0])]
                for cc in chain: toks.append([x for x in (cc[0],cc[1]) if x!=in_tok(*cc)][0])
                chain_counter[f"{in_tok(*chain[0])} -> {' -> '.join(toks[1:])}"] += 1

for p, n in chain_counter.most_common(15):
    print(f"{n:4d}  {p}")
conn.close()