"""Study real swap sizes from the warehouse and determine their distribution.

Samples amount_usd from the unified `swaps` table (the same data the routing
layer uses), then fits candidate distributions (lognormal, power-law/Pareto,
gamma, Weibull, exponential) and reports which best describes the data.

Usage: python3 scratch/swap_size_distribution.py [--sample 200000] [--min 10]
"""
import argparse
import os
import sys
import random

import psycopg2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'api', 'routing'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder', 'include'))
from config import DATA_WAREHOUSE_DB


def fetch_sample(sample_n, min_usd):
    """Fetch a random sample using PostgreSQL TABLESAMPLE."""
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    # TABLESAMPLE SYSTEM samples whole pages (~0.1% of table), then we filter
    # amount_usd >= min. For 71M rows, 0.1% gives ~71k candidates; use more.
    pct = max(0.1, 100.0 * sample_n / 2000000)
    cur.execute("""
        SELECT s.amount_usd
        FROM swaps AS s TABLESAMPLE SYSTEM (%s)
        WHERE s.amount_usd >= %s
        LIMIT %s
    """, (pct, min_usd, sample_n))
    sizes = [r[0] for r in cur.fetchall()]
    conn.close()
    return sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=200000)
    ap.add_argument('--min', type=float, default=10.0)
    args = ap.parse_args()

    sizes = fetch_sample(args.sample, args.min)
    print(f"sampled n={len(sizes):,}  min={min(sizes):,.0f}  max={max(sizes):,.0f}")

    import numpy as np
    from scipy import stats

    x = np.array(sizes, dtype=float)
    x = x[x > 0]
    logx = np.log(x)
    n = len(x)

    print(f"\n=== Descriptive stats (n={n:,}) ===")
    print(f"mean={x.mean():,.1f}  median={np.median(x):,.1f}  p90={np.percentile(x,90):,.1f} "
          f"p99={np.percentile(x,99):,.1f}  max={x.max():,.1f}")
    print(f"log-mean={logx.mean():.3f}  log-std={logx.std():.3f}  skew(log)={stats.skew(logx):.3f}")

    # --- Fit candidates on the raw USD scale ---
    results = []

    # Lognormal
    sh, loc, sc = stats.lognorm.fit(x, floc=0)
    loglike = stats.lognorm.logpdf(x, sh, loc=0, scale=sc).sum()
    results.append(('lognormal', loglike, {'s': sh, 'scale': sc}))

    # Gamma (requires positive)
    g_a, g_loc, g_sc = stats.gamma.fit(x, floc=0)
    loglike = stats.gamma.logpdf(x, g_a, loc=0, scale=g_sc).sum()
    results.append(('gamma', loglike, {'a': g_a, 'scale': g_sc}))

    # Weibull (flexible for heavy-ish tails)
    w_c, w_loc, w_sc = stats.weibull_min.fit(x, floc=0)
    loglike = stats.weibull_min.logpdf(x, w_c, loc=0, scale=w_sc).sum()
    results.append(('weibull', loglike, {'c': w_c, 'scale': w_sc}))

    # Exponential
    e_loc, e_sc = stats.expon.fit(x, floc=0)
    loglike = stats.expon.logpdf(x, loc=0, scale=e_sc).sum()
    results.append(('exponential', loglike, {'scale': e_sc}))

    # Power law: fit slope on log-log via MLE (alpha = 1 + n/sum(ln(x/xmin)))
    xmin = np.percentile(x, 20)  # fit tail above 20th percentile
    tail = x[x >= xmin]
    alpha = 1 + len(tail) / np.sum(np.log(tail / xmin))
    # log-likelihood of Pareto(alpha, xmin) over the tail
    loglike = len(tail) * (np.log(alpha - 1) - np.log(xmin)) - alpha * np.sum(np.log(tail / xmin))
    results.append((f'powerlaw(alpha={alpha:.2f},xmin={xmin:.0f})', loglike, {'alpha': alpha}))

    # Compare via log-likelihood (higher = better) and BIC
    print("\n=== Fit comparison (higher log-likelihood = better) ===")
    print(f"{'distribution':<38} {'loglike':>12} {'BIC':>12}")
    for name, ll, params in sorted(results, key=lambda r: -r[1]):
        k = len(params)
        bic = k * np.log(n) - 2 * ll
        print(f"{name:<38} {ll:>12,.0f} {bic:>12,.0f}")

    # Also report how the 'uniform [min,max]' generator (current Arena default)
    # compares: loglike of a uniform fit on same data.
    lo, hi = x.min(), x.max()
    ll_uniform = n * np.log(1.0 / (hi - lo))
    print(f"{'uniform(50,50000) [Arena default]':<38} {ll_uniform:>12,.0f} {2*np.log(n) - 2*ll_uniform:>12,.0f}")

    # KS test for the two best candidates
    print("\n=== KS tests (D, p-value) ===")
    for name, _, params in sorted(results, key=lambda r: -r[1])[:3]:
        if name.startswith('lognormal'):
            D, p = stats.kstest(x, stats.lognorm.cdf, args=(params['s'], 0, params['scale']))
        elif name.startswith('gamma'):
            D, p = stats.kstest(x, stats.gamma.cdf, args=(params['a'], 0, params['scale']))
        elif name.startswith('weibull'):
            D, p = stats.kstest(x, stats.weibull_min.cdf, args=(params['c'], 0, params['scale']))
        elif name.startswith('exponential'):
            D, p = stats.kstest(x, stats.expon.cdf, args=(0, params['scale']))
        else:
            continue
        print(f"  {name:<38} D={D:.4f}  p={p:.2e}")


if __name__ == '__main__':
    main()
