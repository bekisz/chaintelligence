"""Check for round-number clustering in swap sizes and tail hardness."""
import os, sys
import numpy as np
import psycopg2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'api', 'routing'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder', 'include'))
from config import DATA_WAREHOUSE_DB

conn = psycopg2.connect(DATA_WAREHOUSE_DB)
cur = conn.cursor()
cur.execute("""
    SELECT s.amount_usd FROM swaps AS s TABLESAMPLE SYSTEM (1)
    WHERE s.amount_usd >= 10 LIMIT 500000
""")
x = np.array([r[0] for r in cur.fetchall()])
conn.close()

# Round-number fraction: size near a multiple of 100/1000/10000
def frac_round(m):
    return np.mean(np.abs(x / m - np.round(x / m)) < 1e-6)

print(f"n={len(x):,}")
for m in [100, 250, 500, 1000, 5000, 10000]:
    print(f"  exactly multiple of {m:>7,}: {frac_round(m)*100:.3f}%")

# Fraction of swaps exactly equal to common "bot" sizes
for v in [100, 250, 500, 1000, 5000, 10000, 50000]:
    print(f"  exactly {v:>7,}: {np.mean(x==v)*100:.4f}%")

# Tail hardness: does the CCDF look log-linear (log-normal) or power (Pareto)?
print("\nCCDF at selected thresholds (log-log local slope ~ -alpha in a Pareto tail):")
logx = np.log(x)
for v in [100, 500, 1000, 5000, 10000, 50000, 100000]:
    print(f"  P(> {v:>8,}) = {np.mean(x>v):.6f}   lnP={np.log(np.mean(x>v)):.3f}")
