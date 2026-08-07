"""Characterize the bulk of swap sizes: lognormal body + heavy tail.

Fits a lognormal to the full sample and to the body, examines log-binned
densities, and checks where the lognormal breaks down (the heavy tail).
"""
import os
import sys

import numpy as np
import psycopg2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'api', 'routing'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder', 'include'))
from config import DATA_WAREHOUSE_DB


def fetch_sample(sample_n=400000, min_usd=1.0):
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.amount_usd
        FROM swaps AS s TABLESAMPLE SYSTEM (0.5)
        WHERE s.amount_usd >= %s
        LIMIT %s
    """, (min_usd, sample_n))
    sizes = [r[0] for r in cur.fetchall()]
    conn.close()
    return np.array(sizes, dtype=float)


def main():
    print("sampling (min=1 to see the body)...")
    x = fetch_sample(min_usd=1.0)
    x = x[x > 0]
    n = len(x)
    print(f"n={n:,}  min={x.min():,.2f}  max={x.max():,.0f}  median={np.median(x):,.2f}")

    from scipy import stats
    logx = np.log(x)
    print(f"\nlog(s): mean={logx.mean():.3f} std={logx.std():.3f} skew={stats.skew(logx):.3f}")

    # Full-range lognormal fit
    sh, loc, sc = stats.lognorm.fit(x, floc=0)
    print(f"\nlognormal full-range: s={sh:.3f} scale={sc:,.1f} "
          f"(geomean={sc:,.1f})  ks_D={stats.kstest(x, stats.lognorm.cdf, args=(sh,0,sc))[0]:.4f}")

    # Fit lognormal to the log-space and compare quantiles
    mu, sigma = logx.mean(), logx.std()
    print(f"log-space Normal fit: mu={mu:.3f} sigma={sigma:.3f}")

    # Percentiles predicted by lognormal(mu,sigma) vs observed
    print("\n=== Quantile check: observed vs lognormal(mu,sigma) ===")
    for q in [10, 25, 50, 75, 90, 95, 99]:
        obs = np.percentile(x, q)
        pred = np.exp(mu + sigma * stats.norm.ppf(q / 100.0))
        print(f"  p{q:>3}: observed={obs:>12,.1f}   lognormal={pred:>12,.1f}   ratio={obs/pred:.2f}")

    # Log-binned empirical density to see shape (linear in log-log => power law)
    print("\n=== Log-binned density (log-log slope ~ -(alpha+1) in tail) ===")
    bins = np.logspace(np.log10(x.min()), np.log10(x.max()), 40)
    counts, edges = np.histogram(x, bins=bins)
    mids = np.sqrt(edges[:-1] * edges[1:])
    widths = edges[1:] - edges[:-1]
    dens = counts / (n * widths)
    for i in range(len(mids)):
        if counts[i] == 0:
            continue
        slope = None
        if i > 0 and counts[i - 1] > 0:
            slope = np.log(dens[i] / dens[i - 1]) / np.log(mids[i] / mids[i - 1])
        print(f"  {mids[i]:>12,.0f}  cnt={counts[i]:>7,}  dens={dens[i]:.2e}  logslope={slope if slope is None else f'{slope:+.2f}'}")

    # Where does it start being heavy-tailed vs lognormal? Compare tail to LN.
    mu, sigma = logx.mean(), logx.std()
    print("\n=== Tail check: P(X>v) observed vs lognormal ===")
    for v in [1000, 5000, 10000, 50000, 100000]:
        obs = np.mean(x > v)
        ln = 1 - stats.norm.cdf((np.log(v) - mu) / sigma)
        print(f"  >{v:>9,}: observed={obs:.5f}  lognormal={ln:.5f}  ratio={obs/ln:.2f}")


if __name__ == '__main__':
    main()
