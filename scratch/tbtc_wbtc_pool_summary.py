#!/usr/bin/env python3
"""
Summary tables for the TBTC/WBTC Uniswap V3 pools: volume, fees, TVL, APR.

Table 1: the 3 real pools (on-chain swaps = ground truth for volume; TVL from
         liquidity_pool_history). Fees = volume x fee rate. APR = fees/TVL
         annualized over the window.
Table 2: the counterfactual 4th-pool scenarios from tbtc_wbtc_router_spike:
         simulated diverted volume, fee revenue on that volume, and implied
         APR on the LP's own capital (=$CAP per token x 2).

Read-only against Postgres (DATA_WAREHOUSE_DB).
"""
import argparse
import sys
from datetime import datetime, timezone

import psycopg2

import tbtc_wbtc_router_spike as spike

POOLS = {
    "0.01%": "0x73a38006d23517a1d383c88929b2014f8835b38b",
    "0.05%": "0xdbac78be00503d10ae0074e5e5873a61fc56647c",
    "1.00%": "0x3727ab1416c416e78f80441133834edf419a36d5",
}
FEES = {"0.01%": 1, "0.05%": 5, "1.00%": 100}


def pool_tvl(pool_addresses):
    conn = psycopg2.connect(spike.DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.pool_address, avg(lh.tvl_usd), min(lh.tvl_usd), max(lh.tvl_usd)
        FROM liquidity_pool_history lh JOIN liquidity_pool l ON l.id = lh.pool_id
        WHERE l.pool_address = ANY(%s)
          AND lh.date >= '2026-06-01' AND lh.date < '2026-08-02'
        GROUP BY l.pool_address
        """,
        (list(pool_addresses),),
    )
    rows = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    conn.close()
    return rows


def swaps_volume(start, end):
    conn = psycopg2.connect(spike.DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT "feeTier", count(*), sum(abs("amountUSD"))
        FROM uniswap_v3_swaps
        WHERE (token0_symbol='TBTC' AND token1_symbol='WBTC')
          AND abs(amount0) > 0 AND abs(amount1) > 0 AND "amountUSD" > 0
          AND timestamp >= %s AND timestamp < %s
        GROUP BY "feeTier"
        """,
        (start, end),
    )
    rows = {r[0]: (r[1], float(r[2])) for r in cur.fetchall()}
    conn.close()
    return rows


def table1(start, end):
    days = (end - start).days
    sw = swaps_volume(start, end)
    tvl = pool_tvl(list(POOLS.values()))
    total_vol = sum(v[1] for v in sw.values())
    print(f"Window {start.date()} .. {end.date()} ({days} days) - real pools")
    print(f"{'tier':>6s} {'addr':<42s} {'swaps':>7s} {'vol USD':>14s} "
          f"{'fees USD':>10s} {'TVL avg USD':>13s} {'APR':>7s}")
    for tier, addr in POOLS.items():
        n, vol = sw.get(tier, (0, 0.0))
        fees = vol * FEES[tier] / 10000
        t = tvl.get(addr)
        apr = (fees / t[0] * 365 / days * 100) if t and t[0] else 0.0
        share = 100 * vol / total_vol if total_vol else 0.0
        tup = (tier, addr, n, vol, fees, *(t if t else (0, 0, 0)), apr)
        print(f"{tup[0]:>6s} {tup[1]:<42s} {tup[2]:>7,d} {tup[3]:>14,.0f} "
              f"{tup[4]:>10,.0f} {tup[5]:>13,.0f} {tup[8]:>7.2f}%  "
              f"({share:.1f}% of vol)")
    return days, total_vol


def table2(start, end, swaps, opening_px, p0_usd, p1_usd, total_usd, days):
    print(f"\nCounterfactual 4th pool (max-output router, +/-0.5% band, "
          f"arbitraged price => upper bound)")
    print(f"{'LP capital':>11s} {'fee':>7s} {'diverted USD':>13s} "
          f"{'div %':>7s} {'fee rev USD':>11s} {'APR %':>9s}")
    scenarios = [
        (10000, 0.005), (10000, 0.07), (10000, 0.08), (10000, 0.01),
    ]
    for capital, fee_pct in scenarios:
        res = spike.simulate(capital / 2, 0.5, int(round(fee_pct * 10000)),
                             swaps, opening_px, p0_usd, p1_usd, total_usd)
        apr = res["fee_usd"] / capital * 365 / days * 100 if capital else 0.0
        print(f"${capital:>10,.0f} {fee_pct:>7.3f}% {res['div_usd']:>13,.0f} "
              f"{res['pct']:>7.3f} {res['fee_usd']:>11,.0f} "
              f"{apr:>9.3f}%")
    print("  APR % = 4th-pool fee revenue / (full LP capital) annualized; "
          "fee rev = diverted volume x 4th-pool fee")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-08-02")
    args = ap.parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    days, total_usd = table1(start, end)

    swaps = spike.load_swaps(start, end)
    opening_px, p0_usd, p1_usd = spike.opening_price_and_usd(swaps)
    table2(start, end, swaps, opening_px, p0_usd, p1_usd, total_usd, days)


if __name__ == "__main__":
    main()
