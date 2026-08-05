#!/usr/bin/env python3
"""
Counterfactual router spike for the TBTC/WBTC Uniswap V3 pair.

The real swap demand (from the warehouse `uniswap_v3_swaps` table) is split
across the 3 existing pools (0.01%, 0.05%, 1.00%). This spike asks: if we add a
4th pool, how much of that traffic would a max-expected-output router divert to it?

Model:
  - Router: picks the pool with the highest net output. Since the real router was
    already optimal, each swap's observed output IS the best of the 3 real pools;
    the 4th pool diverts the swap iff its simulated net output exceeds it.
  - 4th pool: a single LP position opened at the initial date -- $CAP of each token
    (TBTC + WBTC) provided in a +/-RANGE% band around the opening price. Fee tier is
    an input. Its liquidity L is constant while the market price is inside the band
    (out-of-range => dry, not competitive). The pool price is assumed arbitraged to
    the market (so L never depletes; fee revenue accrues). This is an upper bound on
    competitiveness.
  - Swap evaluation: exact-in `compute_swap_step` (a faithful port of uniswap-v3-core
    v1.0.0) with the price limit at the range boundary. The 4th pool is not
    competitive if the order would push the price past the boundary (partial fill).

Read-only against Postgres (DATA_WAREHOUSE_DB, default localhost:5433).
"""
import argparse
import math
import os
from datetime import date, datetime, timezone
from fractions import Fraction

import psycopg2

import v3_replay_spike as v3

FEES = {"0.01%": 1e-4, "0.05%": 5e-4, "1%": 1e-2, "0.10%": 1e-3}
DATA_WAREHOUSE_DB = os.getenv(
    "DATA_WAREHOUSE_DB",
    "dbname=chaintelligence user=airflow password=airflow host=localhost port=5433",
)
SCALE = 10 ** 8          # integer token scale for the V3 math port
Q96 = 1 << 96


def sqrt_price_x96(price):
    """sqrt(price) * 2^96 as int, price a Fraction (token1 per token0)."""
    if not isinstance(price, Fraction):
        price = Fraction(price)
    return math.isqrt((price.numerator * (1 << 192)) // price.denominator)


def load_swaps(start, end):
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT timestamp, "feeTier", amount0, amount1, "amountUSD"
        FROM uniswap_v3_swaps
        WHERE (token0_symbol='TBTC' AND token1_symbol='WBTC')
          AND abs(amount0) > 0 AND abs(amount1) > 0 AND "amountUSD" > 0
          AND timestamp >= %s AND timestamp < %s
        ORDER BY timestamp, id
        """,
        (start, end),
    )
    rows = cur.fetchall()
    conn.close()
    swaps = []
    for ts, fee_tier, a0, a1, usd in rows:
        a0f, a1f = float(a0), float(a1)
        if not (math.isfinite(a0f) and math.isfinite(a1f)) or a0f == 0 or a1f == 0:
            continue
        t0_in = a0 > 0
        input_amt = abs(float(a0 if t0_in else a1))
        output_amt = abs(float(a1 if t0_in else a0))
        price = Fraction(abs(a1f)) / Fraction(abs(a0f))
        swaps.append({
            "ts": ts,
            "fee_tier": fee_tier,
            "t0_in": t0_in,
            "input": input_amt,
            "output": output_amt,
            "usd": float(usd),
            "price": price,
        })
    return swaps


def opening_price_and_usd(swaps, n=50):
    """Market price + implied USD per token from the first `n` swaps."""
    px = [s["price"] for s in swaps[:n] if s["price"]]
    med_px = sorted(px)[len(px) // 2] if px else Fraction(1, 1)
    usd_t0 = [s["usd"] / s["input"] for s in swaps[:n] if s["t0_in"]]
    usd_t1 = [s["usd"] / s["input"] for s in swaps[:n] if not s["t0_in"]]
    p0_usd = sorted(usd_t0)[len(usd_t0) // 2] if usd_t0 else 70000.0
    p1_usd = sorted(usd_t1)[len(usd_t1) // 2] if usd_t1 else 70000.0
    return med_px, p0_usd, p1_usd


def build_pool(capital_per_token_usd, range_pct, fee_pips, opening_px, p0_usd, p1_usd):
    """Compute the 4th pool's single-position liquidity L and band sqrt bounds."""
    r = range_pct / 100.0
    s = sqrt_price_x96(opening_px)
    sa = sqrt_price_x96(opening_px / (1 + r))
    sb = sqrt_price_x96(opening_px * (1 + r))
    # deposit amounts in SCALE units: $CAP of each token at the implied USD price
    dx = int(round((capital_per_token_usd / p0_usd) * SCALE))
    dy = int(round((capital_per_token_usd / p1_usd) * SCALE))
    # token0: dx = L * (1/S - 1/Sb) * 2^96 ; token1: dy = L * (S - Sa) / 2^96
    lx = dx * sb * s // ((sb - s) * Q96) if sb > s else 0
    ly = dy * Q96 // (s - sa) if s > sa else 0
    L = min(lx, ly)
    return {"fee": fee_pips, "L": L, "sa": sa, "sb": sb, "s_open": s,
            "dx": dx, "dy": dy, "range_pct": range_pct}


def quote(pool, market_sqrt, t0_in, gross_input_scaled):
    """Net output the 4th pool would give for the full gross input, or None if it
    cannot fill the whole order (price hits the range boundary)."""
    if market_sqrt <= pool["sa"] or market_sqrt >= pool["sb"]:
        return None, None, None            # out of range / dry
    target = pool["sa"] if t0_in else pool["sb"]
    sq_next, _in, out, _fee = v3.compute_swap_step(
        market_sqrt, target, pool["L"], gross_input_scaled, pool["fee"])
    if sq_next == target:
        return None, None, None            # partial fill -> not competitive
    return out, sq_next, gross_input_scaled - _in   # out, post-sqrt, fee paid


def fee_free_price(s):
    """Fee-free executed price for a swap. For a t0-in swap the fee is paid on
    output => executed < marginal => /(1-fee); t1-in pays on input => *(1-fee).
    Still carries the pool's slippage, so not usable as a market anchor alone."""
    fee_frac = FEES.get(s["fee_tier"], 0.0)
    if fee_frac >= 1:
        return s["price"]
    return s["price"] / (1 - fee_frac) if s["t0_in"] else s["price"] * (1 - fee_frac)


def market_prices(swaps, window=50):
    """Estimate the marginal (arbitraged) market price per swap as the median of
    the last `window` fee-free executed prices. Slippage is roughly symmetric
    per direction, so the median cancels it."""
    from statistics import median
    out = []
    for i, s in enumerate(swaps):
        lo = max(0, i - window)
        if i == 0:
            out.append(fee_free_price(s))
            continue
        out.append(median(fee_free_price(x) for x in swaps[lo:i]))
    return out


def simulate(cap, range_pct, fee_pips, swaps, opening_px, p0_usd, p1_usd,
             total_usd, markets=None):
    pool = build_pool(cap, range_pct, fee_pips, opening_px, p0_usd, p1_usd)
    if markets is None:
        markets = market_prices(swaps)
    res = {"L": pool["L"], "div_count": 0, "div_usd": 0.0, "fee_usd": 0.0,
           "in_range": 0, "by_dir": {}, "by_size": {}}
    for s, market_price in zip(swaps, markets):
        market_sqrt = sqrt_price_x96(market_price)
        if pool["sa"] < market_sqrt < pool["sb"]:
            res["in_range"] += 1
        out_q, _, _ = quote(pool, market_sqrt, s["t0_in"], int(round(s["input"] * SCALE)))
        if out_q is None:
            continue
        if out_q > int(round(s["output"] * SCALE)):
            res["div_count"] += 1
            res["div_usd"] += s["usd"]
            res["fee_usd"] += s["usd"] * fee_pips / 1_000_000
            d = "tBTC->WBTC" if s["t0_in"] else "WBTC->tBTC"
            b = res["by_dir"].setdefault(d, [0, 0.0])
            b[0] += 1
            b[1] += s["usd"]
            bucket = "<$100" if s["usd"] < 100 else (
                "$100-1k" if s["usd"] < 1000 else (
                "$1k-10k" if s["usd"] < 10000 else ">$10k"))
            b = res["by_size"].setdefault(bucket, [0, 0.0])
            b[0] += 1
            b[1] += s["usd"]
    res["pct"] = 100 * res["div_usd"] / total_usd if total_usd else 0.0
    return res


def print_result(res, range_pct, n_swaps):
    print(f"  L={res['L']}  in-band {res['in_range']}/{n_swaps} swaps")
    print(f"  DIVERTED: {res['div_count']} swaps, ${res['div_usd']:,.0f} "
          f"({res['pct']:.4f}% of volume)  fee rev ${res['fee_usd']:,.0f}")
    for d, v in res["by_dir"].items():
        if v[0]:
            print(f"    {d}: {v[0]} swaps, ${v[1]:,.0f}")
    if res["by_size"]:
        for k, v in sorted(res["by_size"].items()):
            print(f"      {k:>10s}: {v[0]:>5d} swaps  ${v[1]:>12,.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-08-02")
    ap.add_argument("--cap", type=float, default=5000.0, help="USD of each token deposited")
    ap.add_argument("--range-pct", type=float, default=10.0)
    ap.add_argument("--fee-bps", type=float, default=None, help="4th pool fee in bp")
    ap.add_argument("--sweep-fees", action="store_true",
                    help="run across fee tiers 0.5/1/2/3/4/5/9 bp")
    ap.add_argument("--sweep-cap", action="store_true",
                    help="run across capital sizes at the given fee")
    args = ap.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    swaps = load_swaps(start, end)
    print(f"Loaded {len(swaps)} TBTC/WBTC swaps  {args.start}..{args.end}")

    # observed per-pool volume split (the real router's choices)
    vol = {}
    for s in swaps:
        vol[s["fee_tier"]] = vol.get(s["fee_tier"], 0.0) + s["usd"]
    total_usd = sum(vol.values())
    print("\nObserved pool split (real router):")
    for tier, u in sorted(vol.items()):
        print(f"  {tier:>6s}  {u:>14,.0f} USD  ({100*u/total_usd:.2f}%)")

    opening_px, p0_usd, p1_usd = opening_price_and_usd(swaps)
    print(f"\nOpening price (WBTC/TBTC): {float(opening_px):.5f}  "
          f"implied TBTC=${p0_usd:,.0f}  WBTC=${p1_usd:,.0f}")

    if args.sweep_fees:
        fees = [0.5, 1, 2, 3, 4, 5, 9]
    elif args.sweep_cap:
        fees = [args.fee_bps] if args.fee_bps is not None else [0.5]
    else:
        fees = [args.fee_bps] if args.fee_bps is not None else [5]

    if args.sweep_cap:
        caps = [5000, 50000, 500000, 5000000, 50000000]
        for fb in fees:
            fee_pips = int(round(fb * 100))
            print(f"\n--- capital sweep @ {fb:g} bp fee, +/-{args.range_pct:g}% band ---")
            for cap in caps:
                res = simulate(cap, args.range_pct, fee_pips, swaps,
                               opening_px, p0_usd, p1_usd, total_usd)
                print(f"  cap ${cap:>10,.0f}: L={res['L']:>13,}  "
                      f"diverted {res['div_count']:>6d} swaps  "
                      f"${res['div_usd']:>12,.0f}  ({res['pct']:.4f}%)  "
                      f"fee rev ${res['fee_usd']:,.0f}")
        return

    print(f"\n4th pool: ${args.cap:,.0f} each token, +/-{args.range_pct:g}% band")
    for fb in fees:
        fee_pips = int(round(fb * 100))
        print(f"\n--- 4th pool fee {fb:g} bp (${args.cap:,.0f}/token, "
              f"+/-{args.range_pct:g}%) ---")
        res = simulate(args.cap, args.range_pct, fee_pips, swaps,
                       opening_px, p0_usd, p1_usd, total_usd)
        print_result(res, args.range_pct, len(swaps))


if __name__ == "__main__":
    main()
