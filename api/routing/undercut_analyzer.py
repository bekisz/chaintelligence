"""
Counterfactual "undercut" router analyzer for a token pair.

Given the real swap demand for an exact token pair (from the warehouse `swaps`
table), this models adding a hypothetical extra Uniswap V3 pool and estimates
how much of that traffic a max-expected-output router would divert to it.

Model (two-sided inventory / drift — diverges from scratch/tbtc_wbtc_router_spike.py):
  - Router picks the pool with the highest net output. The observed on-chain
    output of each swap IS the best the existing pools could do; the
    hypothetical pool diverts the swap iff its simulated net output exceeds it.
    When the hypothetical pool exactly ties the observed output (both pools are
    equally good, e.g. an identical clone pool), the router is indifferent and
    the tie is broken by deterministic alternation so each pool captures ~half
    of the tied traffic.
  - Hypothetical pool: a single LP position with $LIQ/2 of each token deposited
    in a +/-RANGE% band around the market price. Its liquidity L is constant
    while the pool's price is inside the band (out-of-range => dry).
  - Drift / inventory: the pool is NOT assumed arbitraged back to the market.
    Its internal price starts at the opening price and moves only when the pool
    actually serves a swap. Heavy one-sided flow drifts the price toward the
    band boundary, draining the pool of the output token in that direction;
    real counter-direction swaps (reverse_swaps) rebalance it. A swap that
    would push the price past the boundary (partial fill), or that arrives when
    the pool is already drained of the required token, is not served.
  - Swap evaluation: exact-in `compute_swap_step` (faithful port of
    uniswap-v3-core v1.0.0) with the price limit at the range boundary and the
    pool's current (drifted) price as the starting point.

Read-only against Postgres (DATA_WAREHOUSE_DB, default localhost:5433).
"""
import math
from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from statistics import median
from typing import Dict, List, Optional

SCALE = 10 ** 8          # integer token scale for the V3 math port
Q96 = 1 << 96


# ---------------------------------------------------------------------------
# Uniswap V3 math (faithful port of uniswap-v3-core v1.0.0)
# ---------------------------------------------------------------------------
def mul_div(a, b, denominator):
    return (a * b) // denominator


def mul_div_rounding_up(a, b, denominator):
    return (a * b + denominator - 1) // denominator


def ceil_div(a, b):
    return (a + b - 1) // b


def get_amount0_delta(sqrt_ratio_a, sqrt_ratio_b, liquidity, round_up):
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    numerator1 = liquidity << 96
    numerator2 = sqrt_ratio_b - sqrt_ratio_a
    assert sqrt_ratio_a > 0
    if round_up:
        return ceil_div(ceil_div(numerator1 * numerator2, sqrt_ratio_b), sqrt_ratio_a)
    return (numerator1 * numerator2 // sqrt_ratio_b) // sqrt_ratio_a


def get_amount1_delta(sqrt_ratio_a, sqrt_ratio_b, liquidity, round_up):
    if sqrt_ratio_a > sqrt_ratio_b:
        sqrt_ratio_a, sqrt_ratio_b = sqrt_ratio_b, sqrt_ratio_a
    if round_up:
        return ceil_div(liquidity * (sqrt_ratio_b - sqrt_ratio_a), Q96)
    return (liquidity * (sqrt_ratio_b - sqrt_ratio_a)) // Q96


def get_next_sqrt_price_from_amount0_rounding_up(sqrt_px96, liquidity, amount, add):
    if amount == 0:
        return sqrt_px96
    numerator1 = liquidity << 96
    if add:
        product = amount * sqrt_px96
        if product // amount == sqrt_px96:
            denominator = numerator1 + product
            if denominator >= numerator1:
                return ceil_div(numerator1 * sqrt_px96, denominator)
        return ceil_div(numerator1, (numerator1 // sqrt_px96) + amount)
    product = amount * sqrt_px96
    assert product // amount == sqrt_px96 and numerator1 > product
    denominator = numerator1 - product
    return ceil_div(numerator1 * sqrt_px96, denominator)


def get_next_sqrt_price_from_amount1_rounding_down(sqrt_px96, liquidity, amount, add):
    if add:
        quotient = (amount * Q96) // liquidity
        return sqrt_px96 + quotient
    quotient = ceil_div(amount * Q96, liquidity)
    assert sqrt_px96 > quotient
    return sqrt_px96 - quotient


def get_next_sqrt_price_from_input(sqrt_px96, liquidity, amount_in, zero_for_one):
    assert sqrt_px96 > 0 and liquidity > 0
    if zero_for_one:
        return get_next_sqrt_price_from_amount0_rounding_up(sqrt_px96, liquidity, amount_in, True)
    return get_next_sqrt_price_from_amount1_rounding_down(sqrt_px96, liquidity, amount_in, True)


def compute_swap_step(sqrt_ratio_current, sqrt_ratio_target, liquidity, amount_remaining, fee_pips):
    """Exact-in single-step swap. Returns (sqrt_next, amount_in, amount_out, fee_amount)."""
    zero_for_one = sqrt_ratio_current >= sqrt_ratio_target
    exact_in = amount_remaining >= 0

    if exact_in:
        amount_remaining_less_fee = mul_div(amount_remaining, 1_000_000 - fee_pips, 1_000_000)
        amount_in = (get_amount0_delta(sqrt_ratio_target, sqrt_ratio_current, liquidity, True)
                     if zero_for_one else
                     get_amount1_delta(sqrt_ratio_current, sqrt_ratio_target, liquidity, True))
        if amount_remaining_less_fee >= amount_in:
            sqrt_ratio_next = sqrt_ratio_target
        else:
            sqrt_ratio_next = get_next_sqrt_price_from_input(
                sqrt_ratio_current, liquidity, amount_remaining_less_fee, zero_for_one)
    else:
        amount_out = (get_amount1_delta(sqrt_ratio_target, sqrt_ratio_current, liquidity, False)
                      if zero_for_one else
                      get_amount0_delta(sqrt_ratio_current, sqrt_ratio_target, liquidity, False))
        if -amount_remaining >= amount_out:
            sqrt_ratio_next = sqrt_ratio_target
        else:
            sqrt_ratio_next = get_next_sqrt_price_from_output(
                sqrt_ratio_current, liquidity, -amount_remaining, zero_for_one)

    max_ = sqrt_ratio_target == sqrt_ratio_next

    if zero_for_one:
        amount_in = (amount_in if (max_ and exact_in) else
                     get_amount0_delta(sqrt_ratio_next, sqrt_ratio_current, liquidity, True))
        amount_out = (amount_out if (max_ and not exact_in) else
                      get_amount1_delta(sqrt_ratio_next, sqrt_ratio_current, liquidity, False))
    else:
        amount_in = (amount_in if (max_ and exact_in) else
                     get_amount1_delta(sqrt_ratio_current, sqrt_ratio_next, liquidity, True))
        amount_out = (amount_out if (max_ and not exact_in) else
                      get_amount0_delta(sqrt_ratio_current, sqrt_ratio_next, liquidity, False))

    if not exact_in and amount_out > -amount_remaining:
        amount_out = -amount_remaining

    if exact_in and sqrt_ratio_next != sqrt_ratio_target:
        fee_amount = amount_remaining - amount_in
    else:
        fee_amount = mul_div_rounding_up(amount_in, fee_pips, 1_000_000 - fee_pips)

    return sqrt_ratio_next, amount_in, amount_out, fee_amount


def get_next_sqrt_price_from_output(sqrt_px96, liquidity, amount_out, zero_for_one):
    assert sqrt_px96 > 0 and liquidity > 0
    if zero_for_one:
        return get_next_sqrt_price_from_amount1_rounding_down(sqrt_px96, liquidity, amount_out, False)
    return get_next_sqrt_price_from_amount0_rounding_up(sqrt_px96, liquidity, amount_out, False)


def sqrt_price_x96(price):
    """sqrt(price) * 2^96 as int, price a Fraction (token1 per token0)."""
    if not isinstance(price, Fraction):
        price = Fraction(price)
    return math.isqrt((price.numerator * (1 << 192)) // price.denominator)


# ---------------------------------------------------------------------------
# Market price estimation
# ---------------------------------------------------------------------------
def fee_fraction_from_bps(fee_bps: Optional[float]) -> float:
    """fee_bps (e.g. 100 for 1%) -> fee fraction (e.g. 0.01)."""
    if fee_bps is None or fee_bps <= 0:
        return 0.0
    return fee_bps / 10000.0


def fee_free_price(price: Fraction, t0_in: bool, fee_frac: float) -> Fraction:
    """Fee-free executed price for a swap. t0-in pays fee on output => /(1-fee);
    t1-in pays on input => *(1-fee). Still carries pool slippage."""
    if fee_frac >= 1:
        return price
    return price / (1 - fee_frac) if t0_in else price * (1 - fee_frac)


def market_prices(swaps: List[Dict], window: int = 50) -> List[Fraction]:
    """Estimate the marginal (arbitraged) market price per swap as the median of
    the last `window` fee-free executed prices. Slippage is roughly symmetric
    per direction, so the median cancels it."""
    out: List[Fraction] = []
    for i, s in enumerate(swaps):
        ff_price = s.get("fee_free_price", s["price"])
        if i == 0:
            out.append(ff_price)
            continue
        lo = max(0, i - window)
        out.append(median(s.get("fee_free_price", s["price"]) for s in swaps[lo:i]))
    return out


def opening_price_and_usd(swaps: List[Dict], n: int = 50):
    """Market price + implied USD per token from the first `n` swaps."""
    px = [s.get("fee_free_price", s["price"]) for s in swaps[:n] if s.get("fee_free_price") or s.get("price")]
    med_px = sorted(px)[len(px) // 2] if px else Fraction(1, 1)
    usd_t0 = [s["usd"] / s["input"] for s in swaps[:n] if s["t0_in"]]
    usd_t1 = [s["usd"] / s["input"] for s in swaps[:n] if not s["t0_in"]]
    p0_usd = sorted(usd_t0)[len(usd_t0) // 2] if usd_t0 else None
    p1_usd = sorted(usd_t1)[len(usd_t1) // 2] if usd_t1 else None
    return med_px, p0_usd, p1_usd


# ---------------------------------------------------------------------------
# Hypothetical pool
# ---------------------------------------------------------------------------
def build_pool(capital_per_token_usd, range_pct, fee_pips, opening_px, p0_usd, p1_usd):
    """Compute the hypothetical pool's single-position liquidity L and band
    sqrt bounds. capital_per_token_usd = total liquidity / 2."""
    r = range_pct / 100.0
    s = sqrt_price_x96(opening_px)
    sa = sqrt_price_x96(opening_px / (1 + r))
    sb = sqrt_price_x96(opening_px * (1 + r))
    dx = int(round((capital_per_token_usd / p0_usd) * SCALE)) if p0_usd else 0
    dy = int(round((capital_per_token_usd / p1_usd) * SCALE)) if p1_usd else 0
    lx = dx * sb * s // ((sb - s) * Q96) if sb > s else 0
    ly = dy * Q96 // (s - sa) if s > sa else 0
    L = min(lx, ly)
    return {"fee": fee_pips, "L": L, "sa": sa, "sb": sb, "s_open": s,
            "dx": dx, "dy": dy, "range_pct": range_pct}


def quote(pool, s_cur, t0_in, gross_input_scaled):
    """Net output the hypothetical pool would give at its CURRENT (drifted)
    price for the full gross input, or None if it cannot fill the whole order.

    The pool is dry of a token when its price is at the corresponding band
    boundary (all liquidity concentrated in the other token). t0_in spends
    token0 (start) and pushes the price toward sa (pool gives out token1);
    !t0_in spends token1 (end) and pushes the price toward sb (pool gives out
    token0). A swap that would push the price all the way to the boundary
    cannot be fully filled and is not competitive."""
    if t0_in and s_cur <= pool["sa"]:
        return None, None, None            # dry of token1
    if not t0_in and s_cur >= pool["sb"]:
        return None, None, None            # dry of token0
    target = pool["sa"] if t0_in else pool["sb"]
    sq_next, _in, out, _fee = compute_swap_step(
        s_cur, target, pool["L"], gross_input_scaled, pool["fee"])
    if sq_next == target:
        return None, None, None            # order would drain the pool -> not competitive
    return out, sq_next, gross_input_scaled - _in   # out, post-sqrt, fee paid


# ---------------------------------------------------------------------------
# Top-level simulation
# ---------------------------------------------------------------------------
def simulate(cap, range_pct, fee_pips, swaps, opening_px, p0_usd, p1_usd,
             total_usd, reverse_swaps=None):
    """Run the two-sided router diversion simulation with a drifting inventory.

    `swaps` are the start->end demand (spends the hypothetical pool's token0);
    `reverse_swaps` (optional) are the end->start demand (spends token1) that
    rebalances the pool. All swaps are processed in chronological order.

    The pool's price starts at the opening price and moves ONLY when it serves a
    swap — it is not automatically arbitraged back to the market. Serving one
    direction accumulates the input token and depletes the output token, drifting
    the pool price toward the band boundary; once the pool is drained of a token
    (price at the boundary) it cannot fully fill that direction's swaps. Only
    real counter-direction swaps push the price back and restore service.

    Returns summary + per-fee-tier / per-pool diverted stats so existing pools
    can be adjusted. `div_*`/`by_pool` track the forward (start->end) direction
    so the existing pool rows can be adjusted; reverse serving is reported
    separately and contributes to `fee_usd` (two-sided fee revenue)."""
    pool = build_pool(cap, range_pct, fee_pips, opening_px, p0_usd, p1_usd)
    res = {"L": pool["L"], "div_count": 0, "div_usd": 0.0, "fee_usd": 0.0,
           "reverse_count": 0, "reverse_usd": 0.0, "reverse_fee_usd": 0.0,
           "in_range": 0, "by_fee_bps": defaultdict(lambda: [0, 0.0]),
           "by_pool": defaultdict(lambda: [0, 0.0])}
    s_cur = pool["s_open"]
    tie_flip = False

    # Merge forward and reverse demand chronologically. `forward` is the
    # direction relative to the hypothetical pool (token0 = start token): a
    # forward swap spends the start token (t0_in), a reverse swap spends the
    # end token (!t0_in). input/output are already normalized by symbol, so
    # direction is independent of each real pool's token ordering.
    events = [(s["ts"], True, s) for s in swaps]
    events += [(s["ts"], False, s) for s in (reverse_swaps or [])]
    events.sort(key=lambda e: e[0])

    for _ts, forward, s in events:
        if pool["sa"] < s_cur < pool["sb"]:
            res["in_range"] += 1
        out_q, sq_next, _ = quote(pool, s_cur, forward, int(round(s["input"] * SCALE)))
        if out_q is None:
            continue
        recorded = int(round(s["output"] * SCALE))
        if out_q > recorded:
            divert = True
        elif out_q == recorded:
            # Exact tie: the real pool and the hypothetical pool give the same
            # net output, so the router is indifferent. Deterministically
            # alternate so each pool captures roughly half of the tied traffic.
            tie_flip = not tie_flip
            divert = tie_flip
        else:
            divert = False
        if not divert:
            continue
        s_cur = sq_next
        fee = s["usd"] * fee_pips / 1_000_000
        res["fee_usd"] += fee
        if forward:
            res["div_count"] += 1
            res["div_usd"] += s["usd"]
            b = res["by_fee_bps"].setdefault(s["fee_bps"], [0, 0.0])
            b[0] += 1
            b[1] += s["usd"]
            pkey = (s.get("cid"), s["fee_bps"], s.get("protocol", "Uniswap V3"), s.get("pool_address", ""))
            bp = res["by_pool"].setdefault(pkey, [0, 0.0])
            bp[0] += 1
            bp[1] += s["usd"]
        else:
            res["reverse_count"] += 1
            res["reverse_usd"] += s["usd"]
            res["reverse_fee_usd"] += fee
    res["pct"] = 100 * res["div_usd"] / total_usd if total_usd else 0.0
    return res


def simulate_two_pools(comp_cap, comp_range_pct, comp_fee_pips,
                       hyp_cap, hyp_range_pct, hyp_fee_pips,
                       swaps, opening_px, p0_usd, p1_usd,
                       total_usd, reverse_swaps=None):
    """Coupled two-pool simulation: BOTH the existing (competitor) pool and the
    hypothetical pool are modeled with the same AMM band math, so no recorded
    on-chain output is needed — each swap is quoted against both pools' current
    drifted prices and routed to whichever returns the higher net output (ties
    broken by deterministic alternation). Both pools' prices drift as they serve.

    Use for parameter experiments (e.g. does a larger- or lower-fee competitor
    capture more volume?): each pool gets its own cap/range/fee tier. Returns
    per-pool stats (`count`/`usd`/`fee_usd` forward + `reverse_*`) plus
    `pct` = the hypothetical pool's share of forward volume."""
    comp = build_pool(comp_cap, comp_range_pct, comp_fee_pips,
                      opening_px, p0_usd, p1_usd)
    hyp = build_pool(hyp_cap, hyp_range_pct, hyp_fee_pips,
                     opening_px, p0_usd, p1_usd)

    def blank():
        return {"count": 0, "usd": 0.0, "fee_usd": 0.0,
                "reverse_count": 0, "reverse_usd": 0.0, "reverse_fee_usd": 0.0,
                "in_range": 0}

    res = {"comp": blank(), "hyp": blank(),
           "L": {"comp": comp["L"], "hyp": hyp["L"]}}
    comp_cur, hyp_cur = comp["s_open"], hyp["s_open"]
    tie_flip = False

    events = [(s["ts"], True, s) for s in swaps]
    events += [(s["ts"], False, s) for s in (reverse_swaps or [])]
    events.sort(key=lambda e: e[0])

    for _ts, forward, s in events:
        if comp["sa"] < comp_cur < comp["sb"]:
            res["comp"]["in_range"] += 1
        if hyp["sa"] < hyp_cur < hyp["sb"]:
            res["hyp"]["in_range"] += 1
        comp_out, comp_sq, _ = quote(comp, comp_cur, forward,
                                     int(round(s["input"] * SCALE)))
        hyp_out, hyp_sq, _ = quote(hyp, hyp_cur, forward,
                                   int(round(s["input"] * SCALE)))
        if comp_out is None and hyp_out is None:
            continue
        if comp_out is None:
            serve_comp = False
        elif hyp_out is None:
            serve_comp = True
        elif comp_out > hyp_out:
            serve_comp = True
        elif hyp_out > comp_out:
            serve_comp = False
        else:
            # Exact tie: both pools are equally good; alternate so each gets ~half.
            tie_flip = not tie_flip
            serve_comp = not tie_flip

        if serve_comp:
            comp_cur = comp_sq
            fee = s["usd"] * comp_fee_pips / 1_000_000
            res["comp"]["fee_usd"] += fee
            if forward:
                res["comp"]["count"] += 1
                res["comp"]["usd"] += s["usd"]
            else:
                res["comp"]["reverse_count"] += 1
                res["comp"]["reverse_usd"] += s["usd"]
                res["comp"]["reverse_fee_usd"] += fee
        else:
            hyp_cur = hyp_sq
            fee = s["usd"] * hyp_fee_pips / 1_000_000
            res["hyp"]["fee_usd"] += fee
            if forward:
                res["hyp"]["count"] += 1
                res["hyp"]["usd"] += s["usd"]
            else:
                res["hyp"]["reverse_count"] += 1
                res["hyp"]["reverse_usd"] += s["usd"]
                res["hyp"]["reverse_fee_usd"] += fee
    res["pct"] = 100 * res["hyp"]["usd"] / total_usd if total_usd else 0.0
    return res
