"""Swap-size distribution analysis for a token route.

Given the USD sizes of real swaps on a route, fits a lognormal to the body, a
Pareto tail via Hill's estimator, and a "lognormal body + Pareto tail"
composite, then returns a log-binned histogram plus the fitted curves as
JSON-serializable data for the frontend to render as pure SVG.

Pure-python (math only, no numpy/scipy) so it runs in the API container.
"""

import math
from typing import Dict, List, Optional


def fit_lognormal(x: List[float]):
    """MLE lognormal fit on log(x). Returns (sigma, scale=exp(mu)), loc=0."""
    logs = [math.log(v) for v in x if v > 0]
    if not logs:
        return 0.0, 0.0
    mu = sum(logs) / len(logs)
    var = sum((l - mu) ** 2 for l in logs) / len(logs)
    return math.sqrt(var), math.exp(mu)


def fit_pareto_tail(x: List[float], q: float = 0.90):
    """Hill-style Pareto exponent over the tail above the q-th percentile."""
    xs = sorted(x)
    if not xs:
        return 0.0, 0.0
    idx = min(len(xs) - 1, max(0, int(round(q * len(xs))) - 1))
    xmin = xs[idx]
    tail = [v for v in xs if v >= xmin]
    if len(tail) < 2 or xmin <= 0:
        return 0.0, xmin
    alpha = len(tail) / sum(math.log(v / xmin) for v in tail)
    return alpha, xmin


def _ln_pdf(v: float, sigma: float, scale: float) -> float:
    if v <= 0 or sigma <= 0 or scale <= 0:
        return 0.0
    z = (math.log(v) - math.log(scale)) / sigma
    return math.exp(-0.5 * z * z) / (v * sigma * math.sqrt(2.0 * math.pi))


def _log_bin_edges(x: List[float], nbins: int) -> List[float]:
    lo = math.log10(x[0])
    hi = math.log10(x[-1])
    if hi <= lo:
        hi = lo + 1.0
    # 10.0 ** log10(v) can round to slightly less than v, which would drop the
    # max sample out of the top bin (v > edges[-1]). Nudge the top edge up a
    # fraction so the largest value always lands inside the last bucket.
    f = 1e-12
    return [10.0 ** (lo + (hi + math.log10(1.0 + f) - lo) * i / nbins)
            for i in range(nbins + 1)]


def _dens_log_for(x: List[float], edges: List[float], n_total: int) -> List[float]:
    """Density per unit log10 for sizes x using shared bin edges.

    n_total is the grand total across all groups, so that per-chain densities
    stack (sum) exactly to the overall histogram density.
    """
    nbins = len(edges) - 1
    lo = math.log10(edges[0])
    hi = math.log10(edges[-1])
    counts = [0] * nbins
    for v in x:
        if v < edges[0] or v > edges[-1]:
            continue
        b = int((math.log10(v) - lo) / (hi - lo) * nbins)
        if b >= nbins:
            b = nbins - 1
        counts[b] += 1
    if n_total <= 0:
        return [0.0] * nbins
    mids = [math.sqrt(edges[i] * edges[i + 1]) for i in range(nbins)]
    widths = [edges[i + 1] - edges[i] for i in range(nbins)]
    return [counts[i] / (n_total * widths[i]) * mids[i] * math.log(10.0)
            for i in range(nbins)]


def _counts_and_sums(x: List[float], edges: List[float], fees: List[float] = None):
    """Per-bin swap counts, total USD sums, and total fees using shared bin edges.

    `fees`, when given, must be parallel to `x` (one fee amount per swap) and is
    summed per bin into a `fees` list. Dynamic fee tiers contribute 0.
    """
    nbins = len(edges) - 1
    lo = math.log10(edges[0])
    hi = math.log10(edges[-1])
    counts = [0] * nbins
    sums = [0.0] * nbins
    fee_bins = [0.0] * nbins
    for i, v in enumerate(x):
        if v < edges[0] or v > edges[-1]:
            continue
        b = int((math.log10(v) - lo) / (hi - lo) * nbins)
        if b >= nbins:
            b = nbins - 1
        counts[b] += 1
        sums[b] += v
        if fees:
            fee_bins[b] += fees[i]
    return counts, sums, fee_bins


def _trim_trailing_empties(edges: List[float], counts: List[float], sums: List[float],
                           dens_log: List[float], fees: List[float] = None) -> tuple:
    """Drop trailing bins whose count is zero so the x-axis ends at the last
    non-empty bucket instead of showing empty tail buckets."""
    nbins = len(counts)
    last = nbins - 1
    while last > 0 and counts[last] == 0:
        last -= 1
    n = last + 1
    if fees is None:
        return edges[:n + 1], counts[:n], sums[:n], dens_log[:n]
    return edges[:n + 1], counts[:n], sums[:n], dens_log[:n], fees[:n]


def _nice_ceil(v: float) -> float:
    """Round v up to a 'nice' number (1/2/5 * 10^k)."""
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10.0 ** exp
    f = v / base
    if f <= 1.0:
        nice = 1.0
    elif f <= 2.0:
        nice = 2.0
    elif f <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return nice * base


def _linear_bin_edges(x: List[float], target_bins: int = 80) -> List[float]:
    """Constant-width bins starting at 0, sized for ~target_bins buckets.

    The width is a 'nice' round number so the x-axis ticks come out clean.
    """
    xmax = x[-1]
    if xmax <= 0:
        return [0.0]
    width = _nice_ceil(xmax / target_bins)
    nbins = max(1, int(math.ceil(xmax / width)))
    return [width * i for i in range(nbins + 1)]


def _counts_and_sums_linear(x: List[float], edges: List[float], fees: List[float] = None):
    """Per-bin counts/sums/fees for constant-width linear bins (first edge is 0)."""
    nbins = len(edges) - 1
    width = edges[1] - edges[0]
    counts = [0] * nbins
    sums = [0.0] * nbins
    fee_bins = [0.0] * nbins
    for i, v in enumerate(x):
        if v < 0:
            continue
        b = int(v // width)
        if b >= nbins:
            b = nbins - 1
        counts[b] += 1
        sums[b] += v
        if fees:
            fee_bins[b] += fees[i]
    return counts, sums, fee_bins


def _fit_and_curves(x: List[float], lo: float, hi: float,
                    curve_points: int) -> Dict:
    sigma, scale = fit_lognormal(x)
    alpha, xmin = fit_pareto_tail(x, 0.90)

    lsizes = [lo + (hi - lo) * i / (curve_points - 1) for i in range(curve_points)]
    ln_curve = [v * math.log(10.0) * _ln_pdf(v, sigma, scale)
                for v in (10.0 ** s for s in lsizes)]
    base = _ln_pdf(xmin, sigma, scale) if xmin > 0 else 0.0
    comp_curve = []
    for v in (10.0 ** s for s in lsizes):
        if v <= xmin or alpha <= 0:
            val = v * math.log(10.0) * _ln_pdf(v, sigma, scale)
        else:
            val = v * math.log(10.0) * base * (v / xmin) ** -(alpha + 1.0)
        comp_curve.append(val)

    return {
        "lognormal": {"s": round(sigma, 4), "scale": round(scale, 2)},
        "pareto": {"alpha": round(alpha, 4), "xmin": round(xmin, 2)},
        "curves": {
            "lsizes": lsizes,
            "ln": ln_curve,
            "composite": comp_curve,
        },
    }


def analyze_sizes(sizes: List[float], nbins: int = 120,
                  curve_points: int = 400) -> Optional[Dict]:
    """Compute the swap-size distribution for a list of positive USD sizes.

    Returns a dict with histogram + fitted curves, or None if there are too
    few valid (> 0) sizes to analyze.
    """
    x = [float(v) for v in sizes if v and v > 0]
    x.sort()
    n = len(x)
    if n < 3:
        return None

    lo = math.log10(x[0])
    hi = math.log10(x[-1])
    edges = _log_bin_edges(x, nbins)
    dens_log = _dens_log_for(x, edges, n)
    counts, sums, _ = _counts_and_sums(x, edges)
    edges, counts, sums, dens_log = _trim_trailing_empties(edges, counts, sums, dens_log)
    nbins = len(counts)
    mids = [math.sqrt(edges[i] * edges[i + 1]) for i in range(nbins)]
    ledges = _linear_bin_edges(x)
    lcounts, lsums, _ = _counts_and_sums_linear(x, ledges)
    ledges, lcounts, lsums, _ = _trim_trailing_empties(ledges, lcounts, lsums, [0.0] * len(lcounts))

    result = {
        "n": n,
        "min": x[0],
        "max": x[-1],
        "histogram": {"edges": edges, "mids": mids, "dens_log": dens_log,
                      "counts": counts, "sums": sums,
                      "linear": {"edges": ledges, "counts": lcounts, "sums": lsums}},
    }

    def pct(p):
        k = max(0, int(round(p * n)) - 1)
        return x[max(0, min(n - 1, k))]

    result["median"] = pct(0.5)
    result["p90"] = pct(0.90)
    result["p99"] = pct(0.99)
    result.update(_fit_and_curves(x, lo, hi, curve_points))
    return result


def analyze_sizes_by_chain(groups: Dict[str, List[float]], nbins: int = 120,
                           curve_points: int = 400,
                           fee_groups: Dict[str, List[float]] = None) -> Optional[Dict]:
    """Compute the swap-size distribution split per chain.

    `groups` maps chain name -> list of USD sizes. All chains share the same
    log-bin edges (derived from the grand min/max) so the per-chain densities
    stack exactly onto the overall histogram. Returns the same shape as
    analyze_sizes plus a "chains" list:
        [{"name": ..., "n": ..., "dens_log": [...]}, ...]
    ordered by descending n, or None if there are too few sizes overall.

    `fee_groups`, when given, is parallel to `groups` (one fee amount per swap,
    same ordering) and is summed per bin into `fees`/`linear_fees` arrays for
    the histogram and each group.
    """
    x = []
    x_fees = []
    per_chain = {}
    per_fees = {}
    for name, vals in groups.items():
        cleaned = [v for v in vals if v and v > 0]
        if not cleaned:
            continue
        fees_for = (fee_groups or {}).get(name) or []
        pairs = sorted(zip(cleaned, [float(fees_for[i]) if i < len(fees_for) else 0.0
                                     for i in range(len(cleaned))]),
                       key=lambda p: p[0])
        per_chain[name] = [p[0] for p in pairs]
        per_fees[name] = [p[1] for p in pairs]
        x.extend(per_chain[name])
        x_fees.extend(per_fees[name])
    x.sort()
    x_fees = [f for _, f in sorted(zip(x, x_fees), key=lambda p: p[0])]
    n = len(x)
    if n < 3 or not per_chain:
        return None

    lo = math.log10(x[0])
    hi = math.log10(x[-1])
    edges = _log_bin_edges(x, nbins)
    mids = [math.sqrt(edges[i] * edges[i + 1]) for i in range(nbins)]

    overall = _dens_log_for(x, edges, n)
    if fee_groups:
        overall_counts, overall_sums, overall_fees = _counts_and_sums(x, edges, x_fees)
        edges, overall_counts, overall_sums, overall, overall_fees = _trim_trailing_empties(
            edges, overall_counts, overall_sums, overall, overall_fees)
    else:
        overall_counts, overall_sums, _ = _counts_and_sums(x, edges)
        edges, overall_counts, overall_sums, overall = _trim_trailing_empties(
            edges, overall_counts, overall_sums, overall)
    nbins = len(overall_counts)
    mids = [math.sqrt(edges[i] * edges[i + 1]) for i in range(nbins)]
    ledges = _linear_bin_edges(x)
    if fee_groups:
        overall_lc, overall_ls, overall_lfees = _counts_and_sums_linear(x, ledges, x_fees)
        ledges, overall_lc, overall_ls, _, overall_lfees = _trim_trailing_empties(
            ledges, overall_lc, overall_ls, [0.0] * len(overall_lc), overall_lfees)
    else:
        overall_lc, overall_ls, _ = _counts_and_sums_linear(x, ledges)
        ledges, overall_lc, overall_ls, _ = _trim_trailing_empties(
            ledges, overall_lc, overall_ls, [0.0] * len(overall_lc))
    chains = []
    for name, chain_x in sorted(per_chain.items(), key=lambda kv: -len(kv[1])):
        chain_fees = per_fees[name]
        logs = [math.log(v) for v in chain_x]
        chain_counts, chain_sums, chain_fees_b = _counts_and_sums(chain_x, edges, chain_fees)
        chain_dens = _dens_log_for(chain_x, edges, n)
        lc, ls, lfees = _counts_and_sums_linear(chain_x, ledges, chain_fees)
        chains.append({
            "name": name,
            "n": len(chain_x),
            "min": chain_x[0],
            "max": chain_x[-1],
            "sum_log": round(sum(logs), 6),
            "sum_log2": round(sum(l * l for l in logs), 6),
            "dens_log": chain_dens,
            "counts": chain_counts,
            "sums": chain_sums,
            "fees": chain_fees_b,
            "linear_counts": lc,
            "linear_sums": ls,
            "linear_fees": lfees,
        })

    hist = {"edges": edges, "mids": mids, "dens_log": overall,
            "counts": overall_counts, "sums": overall_sums,
            "linear": {"edges": ledges, "counts": overall_lc, "sums": overall_ls}}
    if fee_groups:
        hist["fees"] = overall_fees
        hist["linear"]["fees"] = overall_lfees

    result = {
        "n": n,
        "min": x[0],
        "max": x[-1],
        "histogram": hist,
        "chains": chains,
    }

    def pct(p):
        k = max(0, int(round(p * n)) - 1)
        return x[max(0, min(n - 1, k))]

    result["median"] = pct(0.5)
    result["p90"] = pct(0.90)
    result["p99"] = pct(0.99)
    result.update(_fit_and_curves(x, lo, hi, curve_points))
    return result


def main():
    import argparse
    import sys
    import os

    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (os.path.join(_ROOT, 'chain-feeder'),
              os.path.join(_ROOT, 'chain-feeder', 'include')):
        if p not in sys.path:
            sys.path.insert(0, p)
    from config import DATA_WAREHOUSE_DB
    import psycopg2

    ap = argparse.ArgumentParser(description="Swap-size distribution for a route")
    ap.add_argument("start_token", type=str)
    ap.add_argument("end_token", type=str)
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--network", type=str, default=None)
    ap.add_argument("--limit", type=int, default=500000)
    args = ap.parse_args()

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=args.days)
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.amount_usd
        FROM swaps s
        JOIN liquidity_pool lp ON s.pool_id = lp.id
        JOIN chain ch ON lp.chain_id = ch.id
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE s.ts >= %s AND s.ts <= %s
          AND s.amount_usd >= 10.0
          AND ((UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s)
               OR (UPPER(c0.symbol) = %s AND UPPER(c1.symbol) = %s))
        ORDER BY s.ts DESC
        LIMIT %s
    """, (start_dt, end_dt,
          args.start_token.upper(), args.end_token.upper(),
          args.end_token.upper(), args.start_token.upper(), args.limit))
    sizes = [r[0] for r in cur.fetchall()]
    conn.close()

    result = analyze_sizes(sizes)
    if not result:
        print(f"No swap data for {args.start_token}->{args.end_token} "
              f"in last {args.days} days")
        return
    print(f"n={result['n']:,}  median=${result['median']:,.1f}  "
          f"p90=${result['p90']:,.1f}  p99=${result['p99']:,.1f}  "
          f"max=${result['max']:,.0f}")
    print(f"lognormal s={result['lognormal']['s']} "
          f"geomean=${result['lognormal']['scale']:,.1f}")
    print(f"pareto alpha={result['pareto']['alpha']} "
          f"xmin=${result['pareto']['xmin']:,.0f}")


if __name__ == "__main__":
    from datetime import datetime, timedelta
    main()
