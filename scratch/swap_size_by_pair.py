"""Distribution of swap sizes restricted to routing-relevant token pairs.

The route analyzer works on tracked pairs (USDC, USDT, WETH, WBTC + tracked).
Checks whether the same lognormal+heavy-tail shape holds for those subsets.
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


def fetch(pair_filter, min_usd=10.0, limit=200000):
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT s.amount_usd
        FROM swaps AS s TABLESAMPLE SYSTEM (0.5)
        JOIN liquidity_pool lp ON s.pool_id = lp.id
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE s.amount_usd >= {min_usd} AND {pair_filter}
        LIMIT {limit}
    """)
    vals = [r[0] for r in cur.fetchall()]
    conn.close()
    return np.array(vals, dtype=float)


def describe(name, x):
    from scipy import stats
    x = x[x > 0]
    if len(x) == 0:
        print(f"{name}: empty")
        return
    logx = np.log(x)
    sh, loc, sc = stats.lognorm.fit(x, floc=0)
    ks = stats.kstest(x, stats.lognorm.cdf, args=(sh, 0, sc))[0]
    print(f"{name}: n={len(x):,}  median={np.median(x):,.1f}  p90={np.percentile(x,90):,.1f} "
          f"p99={np.percentile(x,99):,.0f}  max={x.max():,.0f}")
    print(f"   lognormal: s={sh:.3f} geomean={sc:,.1f} ks_D={ks:.4f}  logmu={logx.mean():.3f} logsigma={logx.std():.3f}")


def main():
    print("sampling subsets...")
    describe("ALL (min>=10)          ", fetch("TRUE"))
    describe("USDC/USDT/USD pairs    ", fetch("(c0.symbol IN ('USDC','USDT','DAI','USDbC') AND c1.symbol IN ('USDC','USDT','DAI','USDbC'))"))
    describe("USDC-USDT              ", fetch("(c0.symbol='USDC' AND c1.symbol='USDT') OR (c0.symbol='USDT' AND c1.symbol='USDC')"))
    describe("WETH pairs             ", fetch("'WETH' IN (c0.symbol, c1.symbol)"))
    describe("WBTC pairs             ", fetch("'WBTC' IN (c0.symbol, c1.symbol)"))


if __name__ == '__main__':
    main()
