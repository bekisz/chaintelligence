#!/usr/bin/env python3
"""Coupled two-pool undercut experiment.

Simulates an existing (competitor) pool vs. a hypothetical pool, both with the
AMM band model, on realistic random demand (random direction, random volume).
No recorded on-chain price data is needed — the router quotes both pools and
routes each swap to the better fill.

Usage:
  python scratch/undercut_two_pool_sweep.py                       # default sweep
  python scratch/undercut_two_pool_sweep.py --comp-liquidity 200000
  python scratch/undercut_two_pool_sweep.py --swaps 5000 --seed 3
"""
import argparse
import random
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api" / "routing"))
from undercut_analyzer import simulate_two_pools  # noqa: E402

DEFAULT_COMP_LIQUIDITY = [50000, 100000, 200000, 500000, 1000000]


def gen_swaps(n, seed, vol_min=50.0, vol_max=50000.0):
    rng = random.Random(seed)
    fwd, rev = [], []
    for i in range(n):
        usd = rng.uniform(vol_min, vol_max)
        sw = {"ts": i, "input": usd, "output": usd, "usd": usd, "fee_bps": 30,
              "cid": 1, "protocol": "Uniswap V4", "pool_address": "0xCOMP"}
        (fwd if rng.random() < 0.5 else rev).append(sw)
    return fwd, rev


def run(comp_liquidity, hyp_liquidity, comp_fee_pips, hyp_fee_pips,
        range_pct, swaps, reverse_swaps, days=365.0):
    fwd_total = sum(s["usd"] for s in swaps)
    r = simulate_two_pools(comp_liquidity / 2.0, range_pct, comp_fee_pips,
                           hyp_liquidity / 2.0, range_pct, hyp_fee_pips,
                           swaps, Fraction(1, 1), 1.0, 1.0, fwd_total,
                           reverse_swaps=reverse_swaps)

    def apr(pool):
        vol = pool["usd"] + pool["reverse_usd"]
        fee = pool["fee_usd"] + pool["reverse_fee_usd"]
        tvl = comp_liquidity if pool is r["comp"] else hyp_liquidity
        return fee / tvl * (365.0 / days) * 100.0

    return r, apr


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--comp-liquidity", type=float, nargs="*",
                   default=DEFAULT_COMP_LIQUIDITY, help="competitor liquidity (USD)")
    p.add_argument("--hyp-liquidity", type=float, default=100000.0)
    p.add_argument("--comp-fee-bps", type=float, default=30.0)
    p.add_argument("--hyp-fee-bps", type=float, default=30.0)
    p.add_argument("--range-pct", type=float, default=10.0)
    p.add_argument("--swaps", type=int, default=2000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--vol-min", type=float, default=50.0)
    p.add_argument("--vol-max", type=float, default=50000.0)
    args = p.parse_args()

    swaps, rev = gen_swaps(args.swaps, args.seed, args.vol_min, args.vol_max)
    comp_fee = int(round(args.comp_fee_bps * 100))
    hyp_fee = int(round(args.hyp_fee_bps * 100))
    fwd_total = sum(s["usd"] for s in swaps)
    print("demand: %d fwd / %d rev swaps, fwd volume $%.0f (seed=%d)"
          % (len(swaps), len(rev), fwd_total, args.seed))
    print("hyp pool: $%.0f liquidity, %.3g%% fee, +/-%g%% range"
          % (args.hyp_liquidity, args.hyp_fee_bps / 100.0, args.range_pct))
    print()
    print("%12s | %16s %16s %10s | %16s %16s %10s" % (
        "comp liq", "comp vol", "hyp vol", "hyp share",
        "comp fee", "hyp fee", "hyp APR"))
    print("-" * 108)
    for liq in args.comp_liquidity:
        r, apr = run(liq, args.hyp_liquidity, comp_fee, hyp_fee,
                     args.range_pct, swaps, rev)
        comp_vol = r["comp"]["usd"] + r["comp"]["reverse_usd"]
        hyp_vol = r["hyp"]["usd"] + r["hyp"]["reverse_usd"]
        print("%12.0f | %16.0f %16.0f %9.1f%% | %16.2f %16.2f %9.3f%%" % (
            liq, comp_vol, hyp_vol, r["pct"],
            r["comp"]["fee_usd"] + r["comp"]["reverse_fee_usd"],
            r["hyp"]["fee_usd"] + r["hyp"]["reverse_fee_usd"],
            apr(r["hyp"])))


if __name__ == "__main__":
    main()
