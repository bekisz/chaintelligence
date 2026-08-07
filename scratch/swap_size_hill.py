"""Reconcile tail exponent: Hill estimator + lognormal vs power-law on the tail."""
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
x = np.sort(x)
n = len(x)

def hill_alpha(x, xmin):
    tail = x[x >= xmin]
    k = len(tail)
    if k < 100: return None, None
    # Hill: alpha = k / sum(ln(x_i/xmin))
    a = k / np.sum(np.log(tail / xmin))
    se = a / np.sqrt(k)
    return a, se

print("=== Hill estimator (alpha for Pareto tail) ===")
for q in [0.80, 0.85, 0.90, 0.95, 0.97, 0.99]:
    xmin = np.percentile(x, q * 100)
    a, se = hill_alpha(x, xmin)
    if a is not None:
        print(f"  tail above p{q*100:.0f} (>{xmin:>9,.0f}): alpha={a:.3f} +/- {se:.3f}  n_tail={np.sum(x>=xmin):,}")

# The CCDF slope we saw (~-1.1) was BETWEEN quantiles; Hill gives a point estimate.
# Check lognormal: does the log-CDF tail curvature match?
from scipy import stats
mu, sigma = np.log(x).mean(), np.log(x).std()
print(f"\nlognormal body fit: mu={mu:.3f} sigma={sigma:.3f}")
print("Observed CCDF vs lognormal CCDF at thresholds (should diverge if tail is power-law):")
for v in [500, 1000, 5000, 10000, 50000, 100000]:
    obs = np.mean(x > v)
    ln = 1 - stats.norm.cdf((np.log(v) - mu) / sigma)
    print(f"  >{v:>8,}: obs={obs:.5f} logn={ln:.5f} ratio={obs/ln:.2f}")

# Practical: sample from a Pareto with the Hill alpha to see how far you must go
print("\nIf generator used Pareto(xmin=100, alpha) the tail P(>100000) would be:")
for a in [1.1, 1.5, 2.0]:
    print(f"  alpha={a}: {(100/100000)**a*0.5:.4f}")
