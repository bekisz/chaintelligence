"""Reconstruct contiguous WBTC->RLUSD routes since 2026-04-01."""
import psycopg2
conn = psycopg2.connect("host=postgres port=5432 dbname=chaintelligence user=airflow password=airflow")
cur = conn.cursor()

# All txs since cutover touching both WBTC and RLUSD
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
  SELECT 1 FROM swaps s2
  JOIN liquidity_pool lp2 ON s2.pool_id = lp2.id
  JOIN coin c0 ON c0.coin_id = lp2.coin0_id
  JOIN coin c1 ON c1.coin_id = lp2.coin1_id
  WHERE s2.tx_hash = s.tx_hash
    AND (UPPER(c0.symbol)='RLUSD' OR UPPER(c1.symbol)='RLUSD')
);
""")
txs = [r[0] for r in cur.fetchall()]
print(f"total txs touching WBTC+RLUSD: {len(txs)}")

# Fetch all legs for those txs
from collections import defaultdict
legs_by_tx = defaultdict(list)
# chunk to keep ANY() small
for i in range(0, len(txs), 1000):
    chunk = txs[i:i+1000]
    cur.execute("""
        SELECT s.tx_hash, s.log_index,
               UPPER(c0.symbol), UPPER(c1.symbol), s.amount0, s.amount1
        FROM swaps s
        JOIN liquidity_pool lp ON s.pool_id = lp.id
        JOIN coin c0 ON c0.coin_id = lp.coin0_id
        JOIN coin c1 ON c1.coin_id = lp.coin1_id
        WHERE s.tx_hash = ANY(%s)
    """, (chunk,))
    for tx, lg, s0, s1, a0, a1 in cur.fetchall():
        legs_by_tx[tx].append({'s0': s0, 's1': s1, 'a0': a0, 'a1': a1})

def leg_in(l):
    return l['s0'] if (l['a0'] or 0) > 0 else l['s1']
def leg_out(l):
    return l['s1'] if (l['a0'] or 0) > 0 else l['s0']

# Reconstruct contiguous chains: start at a WBTC leg flowing IN->OUT, follow
# out tokens until RLUSD. Keep only routes that chain WBTC->...->RLUSD.
routes = []
for tx, legs in legs_by_tx.items():
    # try starting from each WBTC leg as entry
    for l in legs:
        if l['s0']=='WBTC' or l['s1']=='WBTC':
            start_in = leg_in(l)
            if start_in != 'WBTC':
                continue  # WBTC is the output -> reverse direction (RLUSD->...->WBTC)
            # walk forward
            chain = [l]
            cur_tok = leg_out(l)
            used = {id(l)}
            while cur_tok != 'RLUSD':
                nxt = None
                for l2 in legs:
                    if id(l2) in used: continue
                    if leg_in(l2) == cur_tok:
                        nxt = l2; break
                if nxt is None: break
                used.add(id(nxt))
                chain.append(nxt)
                cur_tok = leg_out(nxt)
            if chain and chain[-1] and (leg_out(chain[-1])=='RLUSD' or 'RLUSD' in (chain[-1]['s0'],chain[-1]['s1'])):
                routes.append((tx, chain))

# Dedup routes per tx (collapse WBTC->USDC->RLUSD where multiple pools),
# keyed by unique token path, counting how many txs share each path.
seen = {}
out = []
for tx, chain in routes:
    toks = [leg_in(chain[0])]
    for leg in chain:
        toks.append(leg_out(leg))
    path = ' -> '.join(toks)
    if path not in seen:
        seen[path] = {'count': 0, 'legs': len(chain), 'txs': []}
    seen[path]['count'] += 1
    seen[path]['txs'].append(tx)

# Also classify every tx: did we find a contiguous WBTC->RLUSD, or only the
# reverse (RLUSD->WBTC), or are the symbols in disconnected legs?
fwd = 0; rev = 0; disconnected = 0
for tx, legs in legs_by_tx.items():
    hit_fwd = any(p for p in seen if tx in seen[p]['txs'])
    if hit_fwd:
        fwd += 1; continue
    # check reverse contiguous RLUSD -> WBTC
    arr = [(leg_in(l), leg_out(l)) for l in legs]
    toks = [x[0] for x in arr] + [arr[-1][1]] if arr else []
    if 'RLUSD' in arr[0][:2] if arr else False:
        rev += 1; continue
    disconnected += 1

print(f"contiguous WBTC->..->RLUSD routes: {len(seen)}")
for p, meta in sorted(seen.items(), key=lambda kv: -kv[1]['count']):
    print(f"  {meta['count']:3d} txs ({meta['legs']} hops)  {p}")

# Diagnose the other txs: are WBTC/RLUSD connected at all (any chain), and
# if not is it because the RLUSD leg is reverse (input RLUSD)?
connected_any = 0
for tx, legs in legs_by_tx.items():
    if any(tx in meta['txs'] for meta in seen.values()):
        continue
    idx = {t: i for i, t in enumerate([leg_in(l) for l in legs] + [leg_out(legs[-1])])}
    if 'WBTC' in idx and 'RLUSD' in idx and idx['WBTC'] < idx['RLUSD']:
        connected_any += 1
print(f"\nremaining txs (not contiguous forward): {len(legs_by_tx) - sum(m['count'] for m in seen.values())}")
conn.close()