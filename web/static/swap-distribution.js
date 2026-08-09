// Swap Distribution: histogram of real swap sizes for a route + fitted curves.

function fmtAxis(v) {
    if (v >= 1e6) return (v / 1e6).toFixed(0) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(0) + "k";
    return "$" + Math.round(v);
}

function fmtStat(v) {
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "k";
    return "$" + v.toFixed(2);
}

function fmtCount(v) {
    if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(0) + "k";
    return Math.round(v).toString();
}

function fullDollar(v) {
    return "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function fmtDensity(v) {
    if (v >= 1000) return (v / 1000).toFixed(1) + "k";
    if (v >= 1) return v.toFixed(2);
    if (v >= 0.001) return (v * 1000).toFixed(1) + "m";
    return v.toExponential(1);
}

const W = 900, H = 480, PAD_L = 70, PAD_B = 40, PAD_R = 20, PAD_T = 20;
const plotW = W - PAD_L - PAD_R;
const plotH = H - PAD_B - PAD_T;
const SVG_NS = "http://www.w3.org/2000/svg";

// Fixed per-chain colors so a chain keeps the same color across queries.
// Brand colors: Base blue, Ethereum grey, BNB dark yellow, Arbitrum light blue.
const CHAIN_COLORS = {
    "Ethereum": "#8b93a3",
    "Arbitrum": "#6fc3f7",
    "Base": "#0052ff",
    "BNB": "#c99700",
    "Optimism": "#ff0420",
    "Polygon": "#8247e5",
    "Solana": "#9945ff",
};

// Chains the user has toggled off in the legend (excluded from analysis).
const excludedChains = new Set();
// Last full (all-chains) response; toggles recompute a subset client-side.
let lastData = null;
let lastDataDirection = "both";
// Y-axis mode: "count" (swaps per log-$) or "volume" (total USD per bin).
let yAxisMode = "volume";
// Bucket layout: "linear" (fixed-width bins from $0) or "log" (geometric bins).
let bucketMode = "log";
// What the stacked groups represent: "chain" (networks) or "direction" (start→end vs end→start).
let groupByMode = "chain";

// X-axis zoom state, kept in "axis units": log10(value) for log buckets,
// dollar value for linear buckets. null/full means show entire range.
let xFullLo = null, xFullHi = null;   // full data domain
let xLo = null, xHi = null;            // currently visible domain
let lastZoomKey = null;                // resets zoom when dataset/mode changes
let dragStartX = null, dragStartLo = null, dragStartHi = null;
let directionFilter = "both";
// Current route tokens (form inputs), used to color direction groups deterministically.
let startTokenInputValue = "";
let endTokenInputValue = "";

// Stable palette for non-chain group labels (e.g. swap directions).
const GROUP_COLORS = ["#0052ff", "#c99700", "#6fc3f7", "#8b93a3", "#ff0420", "#8247e5"];
// Fee tier palette: sorted by tier, low→high.
const FEE_TIER_COLORS = ["#22c55e", "#84cc16", "#eab308", "#f97316", "#ef4444", "#a855f7", "#a3b3c9"];
// Protocol palette: common protocols get stable colors.
const PROTOCOL_COLORS = {
    "Uniswap V3": "#f73ac2",
    "Uniswap V4": "#9b59b6",
    "PancakeSwap V3": "#f0b90b",
    "Aerodrome": "#2dd4bf",
    "Camelot": "#f97316",
    "Balancer": "#6b5ce7",
    "SushiSwap": "#0e0f23",
    "Curve": "#0073ff",
    "Dodo": "#f58b13",
};
function groupHash(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = (h * 33) ^ s.charCodeAt(i);
    return (h >>> 0) % 100000;
}
// Maps a direction group name -> its server-reported direction ("forward" /
// "reverse"). The server tags each dir_chains entry so forward/reverse is
// resolved once (start/end can expand to comma-joined token aliases, e.g. ETH
// -> "STETH,CWETHV3,...").
let dirByLabel = {};
function directionColor(name) {
    if (groupByMode !== "direction") return null;
    const d = dirByLabel[name];
    if (d === "forward") return "#f87171"; // start→end : light red
    if (d === "reverse") return "#4ade80"; // end→start : light green
    return null;
}
// Compact legend label: in direction mode the resolved name is a comma-joined
// token-alias list (e.g. "STETH,CWETHV3,...→USDC"). Show each side's input
// token; append ".family" only when that side resolved to multiple coins so a
// real single token (e.g. USDC) is written plainly. Keeps the full name for
// toggling.
function directionLegendLabel(fullName) {
    if (groupByMode !== "direction") return fullName;
    const d = dirByLabel[fullName];
    if (!d) return fullName;
    const fwd = (startTokenInputValue || "").toUpperCase();
    const rev = (endTokenInputValue || "").toUpperCase();
    const [left, right] = fullName.split("→").map(s => (s || "").trim());
    // In a forward label, left = start (fwd) resolved and right = end (rev)
    // resolved; in a reverse label they are swapped.
    const fwdFamily = d === "forward" ? (left || "").includes(",") : (right || "").includes(",");
    const revFamily = d === "forward" ? (right || "").includes(",") : (left || "").includes(",");
    const s = fwdFamily ? `${fwd}.family` : fwd;
    const e = revFamily ? `${rev}.family` : rev;
    if (d === "forward") return `${s}→${e}`;
    return `${e}→${s}`;
}
function groupColor(name, mode) {
    if (CHAIN_COLORS[name] && (!mode || mode === "chain")) return CHAIN_COLORS[name];
    if (mode === "fee_tier") {
        // Map common fee tiers to a green→red gradient
        const commonTiers = ["0.01%","0.02%","0.05%","0.10%","0.25%","0.30%","0.50%","1.00%","Dynamic"];
        const ti = commonTiers.indexOf(name);
        if (ti >= 0) return FEE_TIER_COLORS[Math.min(ti, FEE_TIER_COLORS.length - 1)];
        return FEE_TIER_COLORS[groupHash(name) % FEE_TIER_COLORS.length];
    }
    if (mode === "protocol") {
        if (PROTOCOL_COLORS[name]) return PROTOCOL_COLORS[name];
        return GROUP_COLORS[groupHash(name) % GROUP_COLORS.length];
    }
    if (mode === "split") {
        // Split = router fanned the queried pair across 2+ pools in one tx.
        return name === "Split" ? "#f97316" : "#22c55e"; // split: orange / non-split: green
    }
    if (mode === "hops") {
        // hops: single-hop green → multi-hop amber/red gradient.
        const m = name.match(/^(\d+) hop/);
        if (m) {
            const hs = parseInt(m[1], 10);
            return ["#22c55e", "#84cc16", "#eab308", "#f97316"][Math.min(hs - 1, 3)];
        }
        return "#ef4444"; // 4+ hops / fallback
    }
    if (mode === "route") {
        if (name === "Others") return "#64748b";
        const ROUTE_COLORS = ["#0052ff", "#22c55e", "#f97316", "#a855f7", "#06b6d4", "#ec4899", "#eab308", "#84cc16", "#3b82f6", "#f43f5e"];
        return ROUTE_COLORS[groupHash(name) % ROUTE_COLORS.length];
    }
    const dir = directionColor(name);
    if (dir) return dir;
    return GROUP_COLORS[groupHash(name) % GROUP_COLORS.length];
}

function chainColor(name) {
    return groupColor(name, groupByMode);
}
function lnPdf(v, sigma, scale) {
    if (v <= 0 || sigma <= 0 || scale <= 0) return 0;
    const z = (Math.log(v) - Math.log(scale)) / sigma;
    return Math.exp(-0.5 * z * z) / (v * sigma * Math.sqrt(2 * Math.PI));
}

function histCounts(dens, edges, mids, n) {
    const counts = [];
    for (let i = 0; i < dens.length; i++) {
        counts.push(dens[i] * (edges[i + 1] - edges[i]) * n / (mids[i] * Math.LN10));
    }
    return counts;
}

function histQuantile(dens, edges, mids, n, p) {
    const counts = histCounts(dens, edges, mids, n);
    const total = counts.reduce((a, b) => a + b, 0);
    const target = p * total;
    let cum = 0;
    for (let i = 0; i < counts.length; i++) {
        const c = counts[i];
        if (c <= 0) continue;
        if (cum + c >= target) {
            const within = (target - cum) / c;
            const l0 = Math.log10(edges[i]);
            const l1 = Math.log10(edges[i + 1]);
            return Math.pow(10, l0 + within * (l1 - l0));
        }
        cum += c;
    }
    return edges[edges.length - 1];
}

function fitParetoFromHist(dens, edges, mids, n, xmin) {
    const counts = histCounts(dens, edges, mids, n);
    let tailCnt = 0, tailSum = 0;
    for (let i = 0; i < counts.length; i++) {
        if (mids[i] <= xmin || counts[i] <= 0) continue;
        tailCnt += counts[i];
        tailSum += counts[i] * Math.log(mids[i] / xmin);
    }
    return { alpha: tailSum > 0 ? tailCnt / tailSum : 0, xmin: xmin };
}

function buildCurves(lo, hi, sigma, scale, alpha, xmin, points = 400) {
    const lsizes = [], ln = [], composite = [];
    for (let i = 0; i < points; i++) {
        const s = lo + (hi - lo) * i / (points - 1);
        const v = Math.pow(10, s);
        lsizes.push(s);
        ln.push(v * Math.LN10 * lnPdf(v, sigma, scale));
        const base = lnPdf(xmin, sigma, scale);
        let val;
        if (v <= xmin || alpha <= 0) {
            val = v * Math.LN10 * lnPdf(v, sigma, scale);
        } else {
            val = v * Math.LN10 * base * Math.pow(v / xmin, -(alpha + 1));
        }
        composite.push(val);
    }
    return { lsizes, ln, composite };
}

// Recompute the distribution for the currently visible (non-excluded) groups
// from the cached all-groups response — no refetch, exact for the histogram
// and lognormal (additive sufficient stats), ~1% for the binned quantiles.
function activeGroups(d) {
    if (groupByMode === "direction") return d.dir_chains || [];
    if (groupByMode === "fee_tier") {
        const tiers = d.fee_tier_chains || [];
        return tiers.sort((a, b) => {
            const pa = parseFloat(a.name) || (a.name === "Dynamic" ? Infinity : 0);
            const pb = parseFloat(b.name) || (b.name === "Dynamic" ? Infinity : 0);
            return pa - pb;
        });
    }
    if (groupByMode === "protocol") return d.protocol_chains || [];
    if (groupByMode === "split") {
        // Order Split before Non-split so the fan-out segment is on top.
        const s = (d.split_chains || []).slice();
        s.sort((a, b) => (a.name === "Split" ? -1 : 1) - (b.name === "Split" ? -1 : 1));
        return s;
    }
    if (groupByMode === "hops") {
        // Order low→high hop count ("1 hop", "2 hops", "3 hops", "4+ hops").
        const h = (d.hops_chains || []).slice();
        h.sort((a, b) => {
            const ha = parseInt(a.name, 10) || (a.name.startsWith("4+") ? 4 : 0);
            const hb = parseInt(b.name, 10) || (b.name.startsWith("4+") ? 4 : 0);
            return ha - hb;
        });
        return h;
    }
    if (groupByMode === "route") {
        const r = (d.route_chains || []).slice();
        r.sort((a, b) => {
            if (a.name === "Others") return 1;
            if (b.name === "Others") return -1;
            return (b.n || 0) - (a.n || 0);
        });
        return r;
    }
    return d.chains || [];
}
function computeSubset(d) {
    const src = activeGroups(d);
    const visible = src.filter(c => !excludedChains.has(c.name));
    if (visible.length === 0) return null;
    const nSub = visible.reduce((s, c) => s + c.n, 0);
    if (nSub < 3) return null;
    const scale = d.n / nSub;
    const nbins = d.histogram.dens_log.length;
    const dens = new Array(nbins).fill(0);
    const counts = new Array(nbins).fill(0);
    const sums = new Array(nbins).fill(0);
    const fees = new Array(nbins).fill(0);
    const lin = d.histogram.linear || null;
    const lnbins = lin ? lin.counts.length : 0;
    const lcounts = new Array(lnbins).fill(0);
    const lsums = new Array(lnbins).fill(0);
    const lfees = new Array(lnbins).fill(0);
    let sumLog = 0, sumLog2 = 0, cmin = Infinity, cmax = -Infinity;
    for (const c of visible) {
        for (let i = 0; i < nbins; i++) {
            dens[i] += c.dens_log[i] * scale;
            counts[i] += c.counts[i];
            sums[i] += c.sums[i];
            fees[i] += c.fees[i] || 0;
        }
        if (lnbins > 0) {
            const lc = c.linear_counts || [];
            const ls = c.linear_sums || [];
            const lf = c.linear_fees || [];
            for (let i = 0; i < lnbins; i++) {
                lcounts[i] += lc[i] || 0;
                lsums[i] += ls[i] || 0;
                lfees[i] += lf[i] || 0;
            }
        }
        sumLog += c.sum_log;
        sumLog2 += c.sum_log2;
        if (c.min < cmin) cmin = c.min;
        if (c.max > cmax) cmax = c.max;
    }
    const mu = sumLog / nSub;
    const sigma = Math.sqrt(Math.max(0, sumLog2 / nSub - mu * mu));
    const lnScale = Math.exp(mu);
    const edges = d.histogram.edges;
    const mids = d.histogram.mids;
    const median = histQuantile(dens, edges, mids, nSub, 0.5);
    const p90 = histQuantile(dens, edges, mids, nSub, 0.90);
    const p99 = histQuantile(dens, edges, mids, nSub, 0.99);
    const pareto = fitParetoFromHist(dens, edges, mids, nSub, p90);
    const curves = buildCurves(Math.log10(cmin), Math.log10(cmax),
                               sigma, lnScale, pareto.alpha, pareto.xmin);
    const groupField = groupByMode === "fee_tier" ? "fee_tier_chains" :
                       groupByMode === "protocol" ? "protocol_chains" :
                       groupByMode === "direction" ? "dir_chains" :
                       groupByMode === "split" ? "split_chains" :
                       groupByMode === "hops" ? "hops_chains" :
                       groupByMode === "route" ? "route_chains" : "chains";
    return {
        n: nSub,
        min: cmin,
        max: cmax,
        median: median,
        p90: p90,
        p99: p99,
        lognormal: { s: sigma, scale: lnScale },
        pareto: pareto,
        histogram: { edges: edges, mids: mids, dens_log: dens, counts: counts, sums: sums,
                      fees: fees,
                      linear: lin ? { edges: lin.edges, counts: lcounts, sums: lsums, fees: lfees } : null },
        [groupField]: visible,
        curves: curves,
    };
}

function applyChainFilter() {
    if (!lastData) return;
    const d = excludedChains.size === 0 ? lastData : computeSubset(lastData);
    if (!d) return;
    renderStats(d);
    drawDistribution(d);
}

function applyZoom() {
    if (!lastData) return;
    applyChainFilter();
    updateZoomResetBtn();
}

function updateZoomResetBtn() {
    const btn = document.getElementById("dist-zoom-reset");
    if (!btn) return;
    const zoomed = xLo !== null && xHi !== null && xFullLo !== null &&
        (Math.abs(xLo - xFullLo) > 1e-12 || Math.abs(xHi - xFullHi) > 1e-12);
    btn.classList.toggle("hidden", !zoomed);
}

function resetZoom() {
    if (xFullLo === null) return;
    xLo = xFullLo;
    xHi = xFullHi;
    applyZoom();
}

// Wheel: zoom the visible x-window around the cursor position.
function onDistWheel(ev) {
    ev.preventDefault();
    if (xLo === null || xHi === null) return;
    const svg = document.getElementById("dist-chart");
    const rect = svg.getBoundingClientRect();
    const px = ev.clientX - rect.left;
    const fraction = Math.max(0, Math.min(1, (px - PAD_L) / plotW));
    const anchor = xLo + fraction * (xHi - xLo);
    const factor = ev.deltaY > 0 ? 1.25 : 1 / 1.25;
    let newLo = anchor - (anchor - xLo) * factor;
    let newHi = anchor + (xHi - anchor) * factor;
    if (newHi - newLo < (xFullHi - xFullLo) / 200) return; // min zoom
    // clamp to full domain
    if (newLo < xFullLo) { newHi = Math.min(xFullHi, newHi + (xFullLo - newLo)); newLo = xFullLo; }
    if (newHi > xFullHi) { newLo = Math.max(xFullLo, newLo - (newHi - xFullHi)); newHi = xFullHi; }
    if (newHi <= newLo) return;
    xLo = newLo; xHi = newHi;
    applyZoom();
}

// Drag: pan the visible window.
function onDistDragStart(ev) {
    if (ev.button !== 0) return;
    const svg = document.getElementById("dist-chart");
    const rect = svg.getBoundingClientRect();
    dragStartX = ev.clientX - rect.left;
    dragStartLo = xLo;
    dragStartHi = xHi;
    svg.classList.add("dragging");
    window.addEventListener("mousemove", onDistDragMove);
    window.addEventListener("mouseup", onDistDragEnd);
}
function onDistDragMove(ev) {
    if (dragStartX === null || xFullLo === null) return;
    const svg = document.getElementById("dist-chart");
    const rect = svg.getBoundingClientRect();
    const px = ev.clientX - rect.left;
    const span = dragStartHi - dragStartLo;
    let dx = (dragStartX - px) / plotW * span;
    let newLo = dragStartLo + dx;
    let newHi = dragStartHi + dx;
    if (newLo < xFullLo) { newHi -= (newLo - xFullLo); newLo = xFullLo; }
    if (newHi > xFullHi) { newLo -= (newHi - xFullHi); newHi = xFullHi; }
    if (newHi <= newLo) return;
    xLo = newLo; xHi = newHi;
    applyZoom();
}
function onDistDragEnd() {
    dragStartX = null;
    const svg = document.getElementById("dist-chart");
    if (svg) svg.classList.remove("dragging");
    window.removeEventListener("mousemove", onDistDragMove);
    window.removeEventListener("mouseup", onDistDragEnd);
}

function showSegmentTooltip(d, ch, i, event) {
    const el = document.getElementById("dist-tooltip");
    if (!el) return;
    if (dragStartX !== null) return;
    const linear = bucketMode === "linear";
    const hist = linear ? d.histogram.linear : d.histogram;
    const lo = fullDollar(hist.edges[i]);
    const hi = fullDollar(hist.edges[i + 1]);

    const segCnt = (linear ? (ch.linear_counts || [])[i] : ch.counts[i]) || 0;
    const segVal = (linear ? (ch.linear_sums || [])[i] : ch.sums[i]) || 0;
    const segFees = (linear ? (ch.linear_fees || [])[i] : ch.fees[i]) || 0;

    const n = d.n;
    const totalVal = (linear ? (d.histogram.linear && d.histogram.linear.sums) : d.histogram.sums).reduce((a, b) => a + b, 0);
    const totalFees = ((linear ? (d.histogram.linear && d.histogram.linear.fees) : d.histogram.fees) || []).reduce((a, b) => a + b, 0);

    const cntPct = n > 0 ? (segCnt / n * 100) : 0;
    const valPct = totalVal > 0 ? (segVal / totalVal * 100) : 0;
    const feePct = totalFees > 0 ? (segFees / totalFees * 100) : 0;

    const color = chainColor(ch.name);
    const label = directionLegendLabel(ch.name);

    el.innerHTML = `
        <div class="tt-header">
            <span class="tt-swatch" style="background:${color};"></span>
            <span class="tt-group-name">${label}</span>
        </div>
        <div class="tt-title">${lo} – ${hi}</div>
        <div class="tt-row"><span>Swaps</span><b>${segCnt.toLocaleString("en-US")} <em>(${cntPct.toFixed(1)}%)</em></b></div>
        <div class="tt-row"><span>Value</span><b>${fullDollar(segVal)} <em>(${valPct.toFixed(1)}%)</em></b></div>
        <div class="tt-row"><span>Fees</span><b>${fullDollar(segFees)} <em>(${feePct.toFixed(1)}%)</em></b></div>
    `;
    positionTooltip(el, event);
}

function positionTooltip(el, event) {
    const box = el.getBoundingClientRect();
    const svgBox = document.getElementById("dist-chart").closest(".dist-chart-box");
    if (!svgBox) return;
    const svgRect = svgBox.getBoundingClientRect();
    let left = event.clientX - svgRect.left + 14;
    let top = event.clientY - svgRect.top - box.height - 10;
    if (left + box.width > svgRect.width - 8) left = svgRect.width - box.width - 8;
    if (left < 8) left = 8;
    if (top < 8) top = event.clientY - svgRect.top + 18;
    el.style.left = left + "px";
    el.style.top = top + "px";
    el.style.opacity = "1";
    el.style.visibility = "visible";
}

function highlightGroup(groupName) {
    const legend = document.getElementById("dist-legend");
    if (legend) {
        legend.querySelectorAll(".chain-toggle").forEach(btn => {
            if (btn.dataset.chain === groupName) {
                btn.classList.add("chain-highlight");
                btn.classList.remove("chain-dimmed");
            } else {
                btn.classList.remove("chain-highlight");
                btn.classList.add("chain-dimmed");
            }
        });
    }

    const svg = document.getElementById("dist-chart");
    if (svg) {
        svg.querySelectorAll("rect.bar-segment").forEach(rect => {
            if (rect.getAttribute("data-group") === groupName) {
                rect.setAttribute("opacity", "1");
                rect.setAttribute("stroke", "#ffffff");
                rect.setAttribute("stroke-width", "1.2");
            } else {
                rect.setAttribute("opacity", "0.3");
                rect.setAttribute("stroke", "rgba(0,0,0,0.25)");
                rect.setAttribute("stroke-width", "0.4");
            }
        });
    }
}

function clearHighlight() {
    hideTooltip();
    const legend = document.getElementById("dist-legend");
    if (legend) {
        legend.querySelectorAll(".chain-toggle").forEach(btn => {
            btn.classList.remove("chain-highlight", "chain-dimmed");
        });
    }

    const svg = document.getElementById("dist-chart");
    if (svg) {
        svg.querySelectorAll("rect.bar-segment").forEach(rect => {
            rect.setAttribute("opacity", "0.85");
            rect.setAttribute("stroke", "rgba(0,0,0,0.25)");
            rect.setAttribute("stroke-width", "0.4");
        });
    }
}

function hideTooltip() {
    const el = document.getElementById("dist-tooltip");
    if (!el) return;
    el.style.opacity = "0";
    el.style.visibility = "hidden";
}

function niceStep(maxVal, target = 5) {
    if (maxVal <= 0) return 1;
    const raw = maxVal / target;
    const exp = Math.floor(Math.log10(raw));
    const base = Math.pow(10, exp);
    const f = raw / base;
    const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return nice * base;
}

function pathOf(xs, ys) {
    let pts = "";
    for (let i = 0; i < xs.length; i++) {
        const y = ys[i];
        if (Number.isFinite(y)) pts += `${xs[i].toFixed(1)},${y.toFixed(1)} `;
    }
    return pts.trim();
}

function drawDistribution(d) {
    const svg = document.getElementById("dist-chart");
    svg.innerHTML = "";

    const hist = d.histogram;
    const linear = bucketMode === "linear";
    const volume = yAxisMode === "volume";
    const fees = yAxisMode === "fees";
    const edges = linear ? hist.linear.edges : hist.edges;
    const counts = linear ? hist.linear.counts : hist.counts;
    const sums = linear ? hist.linear.sums : hist.sums;
    let chains = activeGroups(d);
    const nbins = counts.length;

    // Order groups (chains / fee tiers / protocols) by their total relevance
    // for the current Y axis (descending): USD volume, fees, or swap count.
    // This drives both the stacking order and the legend toggle order so they
    // stay visually aligned.
    const _metricKey = volume
        ? (linear ? "linear_sums" : "sums")
        : fees ? (linear ? "linear_fees" : "fees")
        : (linear ? "linear_counts" : "counts");
    const _metricTally = entry =>
        (entry[_metricKey] || []).reduce((a, b) => a + (b || 0), 0);
    chains = chains.slice().sort((a, b) => _metricTally(b) - _metricTally(a));

    // Establish/keep the zoom domain in axis space. Axis space = log10(v) in
    // log mode, v in linear mode. Reset zoom whenever the input changes.
    const aMin = linear ? edges[0] : Math.log10(edges[0]);
    const aMax = linear ? edges[edges.length - 1] : Math.log10(edges[edges.length - 1]);
    const key = (linear ? "l:" : "g:") + d.n + ":" + d.max;
    if (lastZoomKey !== key) {
        lastZoomKey = key;
        xFullLo = aMin; xFullHi = aMax; xLo = aMin; xHi = aMax;
    }
    function axisToPx(a) { return PAD_L + (a - xLo) / (xHi - xLo) * plotW; }
    const X = v => axisToPx(linear ? v : Math.log10(v));
    const inverseX = px => {
        const a = xLo + (px - PAD_L) / plotW * (xHi - xLo);
        return linear ? a : Math.pow(10, a);
    };

    const valuesFull = volume ? sums : (fees ? (linear ? hist.linear.fees : hist.fees) : counts);
    const yFmt = (volume || fees) ? fmtStat : fmtCount;
    // y-scale from the bars currently visible in the zoomed x-window. A heavy
    // tail (a few enormous swap-size/fee bins) can otherwise flatten the whole
    // distribution, so the top of the axis is set at the ~98th percentile of
    // visible bar values and any wild outliers get clipped at the ceiling.
    let ymax = 1;
    const visibleVals = [];
    for (let i = 0; i < nbins; i++) {
        const a0 = linear ? edges[i] : Math.log10(edges[i]);
        const a1 = linear ? edges[i + 1] : Math.log10(edges[i + 1]);
        if (a1 < xLo || a0 > xHi) continue;
        const v = valuesFull[i];
        if (v > 0) visibleVals.push(v);
        if (v > ymax) ymax = v;
    }
    if (visibleVals.length >= 8) {
        visibleVals.sort((a, b) => a - b);
        const cap = visibleVals[Math.min(visibleVals.length - 1,
                                         Math.floor(visibleVals.length * 0.98))];
        // clamp so a decent fraction of bar height always remains visible, but
        // never shrink below the true max unless a real outlier dominates.
        ymax = cap;
    }
    ymax *= 1.15;
    const ymaxAbs = ymax;
    const Y = v => Math.max(PAD_T, H - PAD_B - (v / ymaxAbs) * plotH);

    // Gridlines + y labels
    for (let g = 0; g < 5; g++) {
        const val = ymax * g / 4;
        const gy = Y(val);
        const line = document.createElementNS(SVG_NS, "line");
        line.setAttribute("x1", PAD_L); line.setAttribute("x2", W - PAD_R);
        line.setAttribute("y1", gy); line.setAttribute("y2", gy);
        line.setAttribute("stroke", "rgba(255,255,255,0.08)");
        svg.appendChild(line);
        const txt = document.createElementNS(SVG_NS, "text");
        txt.setAttribute("x", PAD_L - 8); txt.setAttribute("y", gy + 4);
        txt.setAttribute("text-anchor", "end"); txt.setAttribute("font-size", "11");
        txt.setAttribute("fill", "#a3b3c9");
        txt.textContent = yFmt(val);
        svg.appendChild(txt);
    }

    // X axis ticks within the visible (zoomed) domain
    const axisTicks = [];
    if (linear) {
        const step = niceStep(xHi - xLo);
        for (let v = Math.ceil(xLo / step) * step; v <= xHi; v += step) axisTicks.push(v);
    } else {
        for (let e = 1; e <= 1e15; e *= 10) {
            const l = Math.log10(e);
            if (l >= xLo - 1e-9 && l <= xHi + 1e-9) axisTicks.push(l);
        }
    }
    for (const t of axisTicks) {
        const gx = X(linear ? t : Math.pow(10, t));
        const line = document.createElementNS(SVG_NS, "line");
        line.setAttribute("x1", gx); line.setAttribute("x2", gx);
        line.setAttribute("y1", H - PAD_B); line.setAttribute("y2", H - PAD_B + 4);
        line.setAttribute("stroke", "rgba(255,255,255,0.2)");
        svg.appendChild(line);
        const txt = document.createElementNS(SVG_NS, "text");
        txt.setAttribute("x", gx); txt.setAttribute("y", H - PAD_B + 16);
        txt.setAttribute("text-anchor", "middle"); txt.setAttribute("font-size", "11");
        txt.setAttribute("fill", "#a3b3c9");
        txt.textContent = fmtAxis(linear ? t : Math.pow(10, t));
        svg.appendChild(txt);
    }

    // Stacked histogram bars, one segment per chain.
    const showMidline = groupByMode === "direction" && !linear && chains.length >= 2;
    for (let i = 0; i < nbins; i++) {
        const x0 = X(edges[i]);
        const x1 = X(edges[i + 1]);
        const width = Math.max(x1 - x0, 0.8);
        let cumulative = 0;
        for (const ch of chains) {
            let seg;
            if (volume) {
                seg = (linear ? (ch.linear_sums || [])[i] : ch.sums[i]) || 0;
            } else if (fees) {
                seg = (linear ? (ch.linear_fees || [])[i] : ch.fees[i]) || 0;
            } else {
                seg = (linear ? (ch.linear_counts || [])[i] : ch.counts[i]) || 0;
            }
            if (seg <= 0) continue;
            const yTop = Y(cumulative + seg);
            const yBot = Y(cumulative);
            const rect = document.createElementNS(SVG_NS, "rect");
            rect.setAttribute("class", "bar-segment");
            rect.setAttribute("data-group", ch.name);
            rect.setAttribute("data-bin", i);
            rect.setAttribute("x", x0.toFixed(1));
            rect.setAttribute("y", yTop.toFixed(1));
            rect.setAttribute("width", width.toFixed(1));
            rect.setAttribute("height", Math.max(yBot - yTop, 0.5).toFixed(1));
            rect.setAttribute("fill", chainColor(ch.name));
            rect.setAttribute("stroke", "rgba(0,0,0,0.25)");
            rect.setAttribute("stroke-width", "0.4");
            rect.setAttribute("opacity", "0.85");
            svg.appendChild(rect);
            cumulative += seg;
        }
        // Red midline at 50% of the stacked bar: with two directions this marks
        // the boundary a 50/50 split would sit at, so the drift is visible.
        if (showMidline && cumulative > 0) {
            const midY = Y(cumulative / 2);
            const line = document.createElementNS(SVG_NS, "line");
            line.setAttribute("x1", x0.toFixed(1));
            line.setAttribute("x2", x1.toFixed(1));
            line.setAttribute("y1", midY.toFixed(1));
            line.setAttribute("y2", midY.toFixed(1));
            line.setAttribute("stroke", "#ffb000");
            line.setAttribute("stroke-width", "2.4");
            line.setAttribute("stroke-dasharray", "6 3");
            line.setAttribute("opacity", "1");
            svg.appendChild(line);
        }
    }

    // Transparent hover overlays: smart segment hit testing per bin
    for (let i = 0; i < nbins; i++) {
        const x0 = X(edges[i]);
        const x1 = X(edges[i + 1]);
        const width = Math.max(x1 - x0, 0.8);
        const hit = document.createElementNS(SVG_NS, "rect");
        hit.setAttribute("x", x0.toFixed(1));
        hit.setAttribute("y", PAD_T);
        hit.setAttribute("width", width.toFixed(1));
        hit.setAttribute("height", (H - PAD_B - PAD_T).toFixed(1));
        hit.setAttribute("fill", "transparent");
        hit.setAttribute("pointer-events", "all");
        hit.style.cursor = "crosshair";
        const idx = i;
        hit.addEventListener("mousemove", ev => {
            if (dragStartX !== null) return;
            const svgBox = svg.getBoundingClientRect();
            const mouseY = ev.clientY - svgBox.top;

            let hoveredCh = null;
            const binSegments = [];

            svg.querySelectorAll(`rect.bar-segment[data-bin="${idx}"]`).forEach(r => {
                const yTop = parseFloat(r.getAttribute("y"));
                const height = parseFloat(r.getAttribute("height"));
                const yBot = yTop + height;
                const grpName = r.getAttribute("data-group");
                const chObj = chains.find(c => c.name === grpName);
                if (chObj) {
                    if (mouseY >= yTop - 2 && mouseY <= yBot + 2) {
                        hoveredCh = chObj;
                    }
                    const dist = Math.min(
                        Math.abs(mouseY - yTop),
                        Math.abs(mouseY - yBot),
                        (mouseY >= yTop && mouseY <= yBot) ? 0 : Infinity
                    );
                    binSegments.push({ ch: chObj, dist: dist });
                }
            });

            if (!hoveredCh && binSegments.length > 0) {
                binSegments.sort((a, b) => a.dist - b.dist);
                if (binSegments[0].dist < 30) {
                    hoveredCh = binSegments[0].ch;
                }
            }

            if (hoveredCh) {
                highlightGroup(hoveredCh.name);
                showSegmentTooltip(d, hoveredCh, idx, ev);
            } else {
                clearHighlight();
            }
        });
        hit.addEventListener("mouseleave", () => clearHighlight());
        svg.appendChild(hit);
    }
    clearHighlight();

    // Legend: clickable chain toggles (dimmed when excluded), then model curves
    const legend = document.getElementById("dist-legend");
    const seen = new Set();
    const chainEntries = [];
    chains.forEach(ch => {
        if (!seen.has(ch.name)) { seen.add(ch.name); chainEntries.push(ch); }
    });
    excludedChains.forEach(name => {
        if (!seen.has(name)) { seen.add(name); chainEntries.push({ name: name, n: 0 }); }
    });
    const chainItems = chainEntries.map(ch => {
        const off = excludedChains.has(ch.name);
        return `<button type="button" class="chain-toggle ${off ? "chain-off" : ""}" data-chain="${ch.name}" title="Toggle ${ch.name}">
            <span class="swatch" style="background:${chainColor(ch.name)}"></span> ${directionLegendLabel(ch.name)}
        </button>`;
    }).join("");
    legend.innerHTML = chainItems;
    legend.querySelectorAll(".chain-toggle").forEach(btn => {
        const name = btn.dataset.chain;
        btn.addEventListener("mouseenter", () => {
            highlightGroup(name);
        });
        btn.addEventListener("mouseleave", () => {
            clearHighlight();
        });
        btn.addEventListener("click", () => {
            if (excludedChains.has(name)) {
                excludedChains.delete(name);
            } else {
                const visible = chainEntries.filter(c => !excludedChains.has(c.name));
                if (visible.length <= 1) return;
                excludedChains.add(name);
            }
            applyChainFilter();
        });
    });
}

function renderStats(d) {
    const stats = document.getElementById("dist-stats");
    if (stats) stats.innerHTML = "";

    // Σ Total: the sum of the metric selected on the Y axis across every
    // active (non-excluded) group. Volume => total USD, fees => total fees,
    // count => total swap count.
    const totalEl = document.getElementById("dist-total");
    if (totalEl && d) {
        const linear = bucketMode === "linear";
        const _sum = arr => (arr || []).reduce((a, b) => a + (b || 0), 0);
        let total = 0;
        const hist = d.histogram || {};
        if (yAxisMode === "fees") {
            total = _sum(linear ? (hist.linear && hist.linear.fees) : hist.fees);
        } else if (yAxisMode === "count") {
            total = _sum(linear ? (hist.linear && hist.linear.counts) : hist.counts);
        } else {
            total = _sum(linear ? (hist.linear && hist.linear.sums) : hist.sums);
        }
        totalEl.textContent = (yAxisMode === "volume" || yAxisMode === "fees")
            ? fmtStat(total)
            : fmtCount(total);
    }
}

async function runAnalysis() {
    const analyzeBtn = document.getElementById("analyze-btn");
    const startTokenInput = document.getElementById("start-token");
    const endTokenInput = document.getElementById("end-token");
    const startDateInput = document.getElementById("start-date");
    const endDateInput = document.getElementById("end-date");
    const queryNetworkSelect = document.getElementById("query-network-filter");
    const resultsSection = document.getElementById("dist-results-section");
    const loader = document.getElementById("dist-loader");
    const noDataMsg = document.getElementById("dist-no-data");
    const statusEl = document.getElementById("dist-status");

    const startToken = startTokenInput.value.trim();
    const endToken = endTokenInput.value.trim();
    const startDate = startDateInput.value;
    const endDate = endDateInput.value;
    const selectedNetwork = queryNetworkSelect.value;
    startTokenInputValue = startToken;
    endTokenInputValue = endToken;

    if (!startToken || !endToken) {
        statusEl.textContent = "Please enter both tokens.";
        statusEl.classList.remove("hidden");
        return;
    }

    statusEl.textContent = "";
    statusEl.classList.add("hidden");
    noDataMsg.classList.add("hidden");
    resultsSection.classList.add("hidden");
    loader.classList.remove("hidden");
    analyzeBtn.disabled = true;

    try {
        let url = `/api/swap-distribution?start_token=${encodeURIComponent(startToken)}&end_token=${encodeURIComponent(endToken)}`;
        if (startDate) url += `&start_date=${startDate}`;
        if (endDate) url += `&end_date=${endDate}`;
        if (selectedNetwork && selectedNetwork !== "all") {
            url += `&network=${encodeURIComponent(selectedNetwork)}`;
        }
                url += `&direction=${directionFilter}`;
                url += `&group_by=${groupByMode}`;

        const response = await fetch(url);
        if (!response.ok) {
            let msg = `API request failed with status ${response.status}`;
            try {
                const err = await response.json();
                if (err.detail) msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
            } catch (e) {}
            throw new Error(msg);
        }
        const payload = await response.json();
        const d = payload.data;

        if (!d || !d.histogram) {
            noDataMsg.classList.remove("hidden");
            return;
        }

        lastData = d;
        lastDataDirection = directionFilter;
        dirByLabel = {};
        (d.dir_chains || []).forEach(c => { if (c.direction) dirByLabel[c.name] = c.direction; });
        excludedChains.clear();
        renderStats(d);
        drawDistribution(d);
        resultsSection.classList.remove("hidden");
    } catch (error) {
        statusEl.textContent = error.message || "Error analyzing distribution";
        statusEl.classList.remove("hidden");
    } finally {
        loader.classList.add("hidden");
        analyzeBtn.disabled = false;
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    const startTokenInput = document.getElementById("start-token");
    const endTokenInput = document.getElementById("end-token");
    const startDateInput = document.getElementById("start-date");
    const endDateInput = document.getElementById("end-date");
    const queryNetworkSelect = document.getElementById("query-network-filter");

    // Same token inputs as the Swaps page: autocomplete dropdown with logos,
    // token icon in the input, and the custom chain selector.
    loadTokenMetadata();
    initTokenAutocomplete(startTokenInput);
    initTokenAutocomplete(endTokenInput);
    initCustomChainSelector(queryNetworkSelect);

    function getYesterdayStr() {
        const d = new Date();
        d.setDate(d.getDate() - 1);
        return d.toISOString().split("T")[0];
    }

    // Default dates like the Swaps page.
    const fetchDateRange = async () => {
        try {
            const url = "/api/routes/date-range";
            const response = await fetch(url);
            const range = await response.json();
            if (range.min_date && range.max_date) {
                startDateInput.min = range.min_date;
                startDateInput.max = range.max_date;
                endDateInput.min = range.min_date;
                endDateInput.max = range.max_date;
                const maxDate = range.max_date;
                const todayStr = new Date().toISOString().split("T")[0];
                endDateInput.value = maxDate === todayStr ? getYesterdayStr() : maxDate;
                const endDate = new Date(endDateInput.value);
                const sevenDaysAgo = new Date(endDate);
                sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 6);
                const sevenDaysAgoStr = sevenDaysAgo.toISOString().split("T")[0];
                startDateInput.value = sevenDaysAgoStr >= range.min_date ? sevenDaysAgoStr : range.min_date;
            }
        } catch (e) {
            const yesterday = new Date();
            yesterday.setDate(yesterday.getDate() - 1);
            endDateInput.value = yesterday.toISOString().split("T")[0];
            const sevenDaysAgo = new Date(yesterday);
            sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 6);
            startDateInput.value = sevenDaysAgo.toISOString().split("T")[0];
        }
    };
    await fetchDateRange();

    const analyzeBtn = document.getElementById("analyze-btn");
    analyzeBtn.addEventListener("click", () => {
        directionFilter = arrowDirection();
        excludedChains.clear();
        runAnalysis();
    });

    const ctrlAxis = document.getElementById("ctrl-axis");
    const ctrlBucket = document.getElementById("ctrl-bucket");
    const ctrlGroup = document.getElementById("ctrl-group");

    // Direction follows the arrow between the two token inputs on the main row:
    // forward (→) or both (⇄). swap-distribution has no direction control of
    // its own.
    function arrowDirection() {
        return (window.routeDirection || "forward") === "both" ? "both" : "forward";
    }

    // When the network filter is a single chain, grouping by chain is pointless,
    // so default Group by to direction instead. Multi-chain ("all") keeps chain.
    function applyGroupByDefault() {
        const multiChain = !queryNetworkSelect || queryNetworkSelect.value === "all";
        const wasChain = groupByMode === "chain";
        if (!multiChain && wasChain) {
            ctrlGroup.value = "direction";
            groupByMode = "direction";
        } else if (multiChain && wasChain) {
            // stay on chain
        }
        excludedChains.clear();
        directionFilter = arrowDirection();
        if (lastData && lastDataDirection !== directionFilter) { lastData = null; }
        if (lastData) { applyChainFilter(); }
    }
    applyGroupByDefault();
    if (queryNetworkSelect) {
        queryNetworkSelect.addEventListener("change", applyGroupByDefault);
    }

    ctrlAxis.addEventListener("change", () => {
        yAxisMode = ctrlAxis.value;
        applyChainFilter();
    });
    ctrlBucket.addEventListener("change", () => {
        bucketMode = ctrlBucket.value;
        applyChainFilter();
    });
    ctrlGroup.addEventListener("change", () => {
        groupByMode = ctrlGroup.value;
        excludedChains.clear();
        if (lastData) {
            applyChainFilter();
        } else {
            runAnalysis();
        }
    });

    // Direction follows the arrow: clicking it re-runs the shared Analyze
    // button, so the distribution's directionFilter is read fresh here via
    // arrowDirection() during the next fetch.
    const distSvg = document.getElementById("dist-chart");
    distSvg.addEventListener("wheel", onDistWheel, { passive: false });
    distSvg.addEventListener("mousedown", onDistDragStart);
    document.getElementById("dist-zoom-reset").addEventListener("click", resetZoom);
});
