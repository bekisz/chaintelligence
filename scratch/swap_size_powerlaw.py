"""Rigorous power-law tail analysis (Clauset-Shalizi-Newman 2009 style).

Fits a Pareto/power-law tail over candidate xmin values, picks the best xmin
by KS distance, then compares the power law against lognormal/gamma/exponential
over the same tail via log-likelihood.

Usage: python3 scratch/swap_size_powerlaw.py
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


def fetch_sample(sample_n=400000, min_usd=10.0):
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


def mle_alpha(x, xmin):
    """MLE for Pareto slope given a cutoff xmin."""
    x = x[x >= xmin]
    if len(x) < 10:
        return None, None
    alpha = 1 + len(x) / np.sum(np.log(x / xmin))
    # standard error via (alpha-1)/sqrt(n)
    se = (alpha - 1) / np.sqrt(len(x))
    return alpha, se


def ks_stat(x, xmin, alpha):
    """KS distance between empirical tail CDF and Pareto(xmin, alpha)."""
    tail = np.sort(x[x >= xmin])
    n = len(tail)
    if n == 0:
        return 1.0
    cdf = 1 - (tail / xmin) ** (-alpha)
    emp = (np.arange(1, n + 1)) / n
    return np.max(np.abs(emp - cdf))


def main():
    print("sampling...")
    x = fetch_sample()
    x = x[x > 0]
    n = len(x)
    print(f"n={n:,}  min={x.min():,.0f}  max={x.max():,.0f}  median={np.median(x):,.0f}")

    # --- Clauset: scan xmin, minimize KS distance ---
    print("\n=== Power-law tail scan (xmin -> alpha, KS) ===")
    cand_xmins = np.unique(np.percentile(x, np.linspace(10, 90, 33)))
    best = None
    for xmin in cand_xmins:
        alpha, se = mle_alpha(x, xmin)
        if alpha is None:
            continue
        ks = ks_stat(x, xmin, alpha)
        marker = ''
        if best is None or ks < best[2]:
            best = (xmin, alpha, ks)
        print(f"  xmin={xmin:>10,.0f}  alpha={alpha:>6.3f} +/- {se:.3f}  KS={ks:.4f}  n_tail={np.sum(x>=xmin):>7,}")

    xmin_best, alpha_best, ks_best = best
    print(f"\n=== Best power-law tail ===")
    print(f"xmin={xmin_best:,.0f}  alpha={alpha_best:.3f}  KS={ks_best:.4f}  "
          f"n_tail={np.sum(x>=xmin_best):,} ({100*np.sum(x>=xmin_best)/n:.1f}% of sample)")

    # --- Compare against lognormal on the same tail ---
    from scipy import stats
    tail = x[x >= xmin_best]
    lt = np.log(tail)

    def ll_pl(tail, xmin, alpha):
        return len(tail) * (np.log(alpha - 1) - np.log(xmin)) - alpha * np.sum(np.log(tail / xmin))

    ll_pl = ll_pl(tail, xmin_best, alpha_best)
    k_pl = 1

    # lognormal fit on tail
    sh, loc, sc = stats.lognorm.fit(tail, floc=0)
    ll_ln = stats.lognorm.logpdf(tail, sh, loc=0, scale=sc).sum()
    k_ln = 2

    # gamma fit on tail
    ga, gloc, gsc = stats.gamma.fit(tail, floc=0)
    ll_gam = stats.gamma.logpdf(tail, ga, loc=0, scale=gsc).sum()
    k_gam = 2

    # weibull
    wc, wloc, wsc = stats.weibull_min.fit(tail, floc=0)
    ll_w = stats.weibull_min.logpdf(tail, wc, loc=0, scale=wsc).sum()
    k_w = 2

    # exponential on tail
    eloc, esc = stats.expon.fit(tail, floc=0)
    ll_e = stats.expon.logpdf(tail, loc=0, scale=esc).sum()
    k_e = 1

    print("\n=== Model comparison on tail [x>={:,.0f}] (loglike / BIC) ===".format(xmin_best))
    n_t = len(tail)
    models = [
        (f'powerlaw alpha={alpha_best:.3f}', ll_pl, k_pl),
        (f'lognormal s={sh:.3f} scale={sc:,.0f}', ll_ln, k_ln),
        (f'gamma a={ga:.3f} scale={gsc:,.0f}', ll_gam, k_gam),
        (f'weibull c={wc:.3f} scale={wsc:,.0f}', ll_w, k_w),
        (f'exponential scale={esc:,.0f}', ll_e, k_e),
    ]
    for name, ll, k in sorted(models, key=lambda r: -r[1]):
        bic = k * np.log(n_t) - 2 * ll
        print(f"  {name:<40} {ll:>14,.0f}   BIC={bic:>12,.0f}")

    print("\n=== Practical takeaway for the Pool Arena generator ===")
    # For a swap-size generator, sample sizes as 10^U where U ~ ? ; or sample a
    # Pareto tail. Report the implied values.
    print(f"Pareto: P(size > v) = (v/xmin)^-{alpha_best:.2f} for v >= {xmin_best:,.0f}")
    print(f"Fraction of swaps above median ~= (74/{xmin_best:,.0f})^-{alpha_best:.2f} "
          f"= {(74/xmin_best)**-alpha_best:.3f}")


if __name__ == '__main__':
    main()
