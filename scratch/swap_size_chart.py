"""Generate a self-contained HTML chart of swap-size histogram + fitted curves.

Samples amount_usd from the warehouse, builds a log-binned histogram, fits a
lognormal to the body, a Pareto tail, and a "lognormal + Pareto tail" composite,
then emits a standalone HTML file with pure-SVG curves (no external libs).

Usage:
    python3 scratch/swap_size_chart.py [--out scratch/swap_size_chart.html] [--min 10] [--limit 500000]
"""
import argparse
import os
import sys

import numpy as np
import psycopg2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'api', 'routing'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder'))
sys.path.insert(0, os.path.join(_ROOT, 'chain-feeder', 'include'))
from config import DATA_WAREHOUSE_DB


def fetch_sample(limit=500000, min_usd=10.0):
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.amount_usd FROM swaps AS s TABLESAMPLE SYSTEM (1)
        WHERE s.amount_usd >= %s LIMIT %s
    """, (min_usd, limit))
    sizes = [r[0] for r in cur.fetchall()]
    conn.close()
    return np.array(sizes, dtype=float)


def fit_lognormal(x):
    from scipy import stats
    sh, loc, sc = stats.lognorm.fit(x, floc=0)
    return sh, sc  # loc always 0 here


def fit_pareto_tail(x, q=0.90):
    """Hill-style exponent over the tail above the q-th percentile."""
    xmin = np.percentile(x, q * 100)
    tail = x[x >= xmin]
    alpha = len(tail) / np.sum(np.log(tail / xmin))
    return alpha, xmin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(_ROOT, 'scratch', 'swap_size_chart.html'))
    ap.add_argument('--min', type=float, default=10.0)
    ap.add_argument('--limit', type=int, default=500000)
    ap.add_argument('--pairs', type=str, default='ALL', help='ALL | stable | weth | wbtc')
    args = ap.parse_args()

    if args.pairs == 'ALL':
        x = fetch_sample(args.limit, args.min)
    else:
        x = fetch_sample_pairs(args.limit, args.min, args.pairs)
    x = x[x > 0]
    n = len(x)

    s, scale = fit_lognormal(x)
    alpha, xmin = fit_pareto_tail(x, 0.90)

    # Log-binned histogram (density per unit of log10(size)).
    lo = np.log10(x.min())
    hi = np.log10(x.max())
    nbins = 60
    edges = np.logspace(lo, hi, nbins + 1)
    counts, _ = np.histogram(x, bins=edges)
    mids = np.sqrt(edges[:-1] * edges[1:])
    widths = edges[1:] - edges[:-1]
    dens = counts / (n * widths)  # density per $, but for log-x display use dens*mids*ln10

    # Convert to density per log10 unit: d(ln x) = dx/x  =>  p(log10) = x*ln10*p(x)
    dens_log = dens * mids * np.log(10)

    # Model curves in log10 space.
    def ln_pdf(v, s_, scale_):
        from scipy import stats
        return stats.lognorm.pdf(v, s_, loc=0, scale=scale_)

    # Composite: lognormal body up to xmin, Pareto(alpha, xmin) tail above.
    def composite_pdf(v):
        pdf_ln = ln_pdf(v, s, scale)
        if v <= xmin:
            return pdf_ln
        # Pareto density: base * (v/xmin)^-(alpha+1), scaled to match at xmin.
        base = ln_pdf(xmin, s, scale)
        return base * (v / xmin) ** -(alpha + 1)

    lsizes = np.linspace(lo, hi, 400)
    ln_curve = np.array([v * np.log(10) * ln_pdf(v, s, scale) for v in 10.0 ** lsizes])
    # Composite only drawn for v >= xmin so the green lognormal body stays visible.
    comp_curve = np.array([
        (v * np.log(10) * composite_pdf(v)) if v >= xmin else np.nan
        for v in 10.0 ** lsizes])

    print(f"n={n:,}  min={x.min():,.1f}  max={x.max():,.0f}  median={np.median(x):,.1f}")
    print(f"lognormal: s={s:.3f} geomean={scale:,.1f}")
    print(f"pareto tail: alpha={alpha:.3f} xmin={xmin:,.0f}")

    emit_html(args.out, x, mids, dens_log, edges, lsizes, ln_curve, comp_curve,
              s, scale, alpha, xmin, args.pairs)


def emit_html(path, x, mids, dens_log, edges, lsizes, ln_curve, comp_curve,
              s, scale, alpha, xmin, pairs):
    W, H, PAD_L, PAD_B, PAD_R, PAD_T = 900, 480, 70, 40, 20, 20
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_B - PAD_T

    lo, hi = np.log10(x.min()), np.log10(x.max())
    ymax = max(dens_log.max() * 1.15, ln_curve.max() * 1.15)

    def X(v):
        return PAD_L + (np.log10(v) - lo) / (hi - lo) * plot_w

    def Y(v):
        return H - PAD_B - (v / ymax) * plot_h

    def path_of(xs, ys):
        pts = "".join(f"{x:.1f},{y:.1f} " for x, y in zip(xs, ys) if y == y)
        return pts.strip()

    # Bars
    bars = []
    for i, (m, d) in enumerate(zip(mids, dens_log)):
        x0 = X(edges[i])
        x1 = X(edges[i + 1])
        y = Y(d)
        bars.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{max(x1-x0,0.8):.1f}" height="{H-PAD_B-y:.1f}" '
                    f'fill="rgba(78,140,255,0.35)" stroke="rgba(78,140,255,0.6)" stroke-width="0.5"/>')

    # Gridlines + labels
    grid = []
    for g in range(0, 5):
        val = ymax * g / 4
        gy = Y(val)
        grid.append(f'<line x1="{PAD_L}" x2="{W-PAD_R}" y1="{gy}" y2="{gy}" stroke="rgba(255,255,255,0.08)"/>')
        grid.append(f'<text x="{PAD_L-8}" y="{gy+4}" text-anchor="end" font-size="11" fill="#a3b3c9">{fmt_y(val)}</text>')
    for e in [1, 10, 100, 1000, 10000, 100000, 1000000, 10000000]:
        if e < x.min() or e > x.max():
            continue
        gx = X(e)
        grid.append(f'<line x1="{gx}" x2="{gx}" y1="{H-PAD_B}" y2="{H-PAD_B+4}" stroke="rgba(255,255,255,0.2)"/>')
        grid.append(f'<text x="{gx}" y="{H-PAD_B+16}" text-anchor="middle" font-size="11" fill="#a3b3c9">{fmt_axis(e)}</text>')

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Swap Size Distribution — {pairs}</title>
<style>
  body {{ margin: 0; background: #0d1117; color: #e6edf3; font-family: Outfit, -apple-system, sans-serif; }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 32px 16px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .sub {{ color: #a3b3c9; font-size: 0.85rem; margin-bottom: 20px; }}
  .card {{ background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 14px; padding: 18px; }}
  svg {{ width: 100%; height: auto; display: block; }}
  .legend {{ display: flex; gap: 18px; flex-wrap: wrap; margin-top: 14px; font-size: 0.82rem; color: #a3b3c9; }}
  .legend span {{ display: flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 18px; height: 3px; border-radius: 2px; display: inline-block; }}
  .stats {{ margin-top: 16px; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
  .stat {{ background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 10px 12px; }}
  .stat b {{ display: block; font-size: 1.05rem; }}
  .stat span {{ font-size: 0.72rem; color: #a3b3c9; }}
</style></head>
<body><div class="wrap">
  <h1>Swap Size Distribution — {pairs}</h1>
  <div class="sub">Log-binned histogram of {x.size:,} real swaps (amount_usd &ge; {x.min():.0f}) vs fitted models. Density per unit of log&#8321;&#8320;(size).</div>

  <div class="card">
    <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none">
      {''.join(grid)}
      {''.join(bars)}
      <polyline points="{path_of(X(10**lsizes), Y(ln_curve))}" fill="none" stroke="#22c55e" stroke-width="2.2" stroke-linejoin="round"/>
      <polyline points="{path_of(X(10**lsizes), Y(comp_curve))}" fill="none" stroke="#f59e0b" stroke-width="2.2" stroke-linejoin="round" stroke-dasharray="5 4"/>
    </svg>
    <div class="legend">
      <span><span class="swatch" style="background:#4e8cff"></span> observed histogram</span>
      <span><span class="swatch" style="background:#22c55e"></span> lognormal (s={s:.2f}, geomean=${scale:,.0f})</span>
      <span><span class="swatch" style="background:#f59e0b"></span> lognormal body + Pareto tail (&#945;={alpha:.2f}, xmin=${xmin:,.0f})</span>
    </div>
  </div>

  <div class="stats">
    <div class="stat"><b>{x.size:,}</b><span>swaps sampled</span></div>
    <div class="stat"><b>${np.median(x):,.0f}</b><span>median</span></div>
    <div class="stat"><b>${np.percentile(x,90):,.0f}</b><span>p90</span></div>
    <div class="stat"><b>${np.percentile(x,99):,.0f}</b><span>p99</span></div>
    <div class="stat"><b>${x.max():,.0f}</b><span>max</span></div>
    <div class="stat"><b>s={s:.2f}</b><span>lognormal sigma (log-$)</span></div>
    <div class="stat"><b>&#945;={alpha:.2f}</b><span>Pareto tail exponent (Hill)</span></div>
    <div class="stat"><b>${xmin:,.0f}</b><span>tail cutoff xmin (p90)</span></div>
  </div>
</div></body></html>
"""
    with open(path, 'w') as f:
        f.write(html)
    print(f"wrote {path}")


def fmt_y(v):
    if v >= 1000: return f"{v/1000:.1f}k"
    if v >= 1: return f"{v:.2f}"
    if v >= 0.001: return f"{v*1000:.1f}m"
    return f"{v:.1e}"


def fmt_axis(v):
    if v >= 1000000: return f"{v/1000000:.0f}M"
    if v >= 1000: return f"{v/1000:.0f}k"
    return f"${v:.0f}"


def fetch_sample_pairs(limit, min_usd, kind):
    conn = psycopg2.connect(DATA_WAREHOUSE_DB)
    cur = conn.cursor()
    if kind == 'stable':
        cond = "(c0.symbol IN ('USDC','USDT','DAI','USDbC') AND c1.symbol IN ('USDC','USDT','DAI','USDbC'))"
    elif kind == 'weth':
        cond = "'WETH' IN (c0.symbol, c1.symbol)"
    elif kind == 'wbtc':
        cond = "'WBTC' IN (c0.symbol, c1.symbol)"
    else:
        cond = "TRUE"
    cur.execute(f"""
        SELECT s.amount_usd
        FROM swaps AS s TABLESAMPLE SYSTEM (1)
        JOIN liquidity_pool lp ON s.pool_id = lp.id
        JOIN coin c0 ON lp.coin0_id = c0.coin_id
        JOIN coin c1 ON lp.coin1_id = c1.coin_id
        WHERE s.amount_usd >= {min_usd} AND {cond} LIMIT {limit}
    """)
    vals = [r[0] for r in cur.fetchall()]
    conn.close()
    return np.array(vals, dtype=float)


if __name__ == '__main__':
    main()
