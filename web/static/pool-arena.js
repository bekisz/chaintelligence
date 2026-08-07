// Pool Arena: coupled N-pool simulation on generated swap demand.
const PALETTE = ["#4e8cff", "#22c55e", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444", "#84cc16"];

const DEFAULT_POOLS = [
    { name: "Baseline", liquidity: 100000, range: 10, feePct: 0.3 },
    { name: "Deep", liquidity: 500000, range: 10, feePct: 0.3 },
    { name: "Cheap", liquidity: 100000, range: 10, feePct: 0.05 },
];

let poolRows = [];

function fmtNum(v) {
    return v.toLocaleString("en-US");
}

function parseThousands(v, fallback) {
    if (v == null) return fallback;
    const f = parseFloat(String(v).replace(/,/g, ""));
    return Number.isFinite(f) ? f : fallback;
}

function parseDecimal(v, fallback) {
    if (v == null) return fallback;
    const f = parseFloat(String(v).replace(/,/g, "."));
    return Number.isFinite(f) ? f : fallback;
}

function fmtUsd(v) {
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (v >= 1e3) return "$" + (v / 1e3).toFixed(1) + "k";
    return "$" + v.toFixed(2);
}

function colorFor(i) {
    return PALETTE[i % PALETTE.length];
}

function renderPoolRows() {
    const list = document.getElementById("pool-list");
    list.innerHTML = "";
    poolRows.forEach((pool, i) => {
        const row = document.createElement("div");
        row.className = "arena-pool-row";
        row.innerHTML = `
            <input type="text" class="pool-name token-input-field" value="${pool.name}" placeholder="Name">
            <input type="color" class="pool-color" value="${colorFor(i)}" title="Chart color">
            <input type="text" inputmode="numeric" class="pool-liq token-input-field" value="${fmtNum(pool.liquidity)}" title="Liquidity $">
            <input type="text" inputmode="decimal" class="pool-fee token-input-field" value="${pool.feePct}" title="Fee %">
            <input type="text" inputmode="decimal" class="pool-range token-input-field" value="${pool.range}" title="Range %">
            <button class="remove-pool-btn" title="Remove">×</button>
        `;
        row.querySelector(".remove-pool-btn").addEventListener("click", () => {
            poolRows.splice(i, 1);
            if (poolRows.length < 2) {
                poolRows.push({ ...DEFAULT_POOLS[poolRows.length] });
            }
            renderPoolRows();
        });
        list.appendChild(row);
    });
    updateLegend();
}

function readPoolRows() {
    return [...document.querySelectorAll(".arena-pool-row")].map((row, i) => ({
        name: row.querySelector(".pool-name").value.trim() || ("Pool " + (i + 1)),
        color: row.querySelector(".pool-color").value,
        liquidity: parseThousands(row.querySelector(".pool-liq").value, 100000),
        feePct: parseDecimal(row.querySelector(".pool-fee").value, 0),
        range: parseDecimal(row.querySelector(".pool-range").value, 10),
    }));
}

function updateLegend() {
    const legend = document.getElementById("arena-legend");
    legend.innerHTML = "";
    readPoolRows().forEach(p => {
        const item = document.createElement("span");
        item.className = "legend-item";
        item.innerHTML = `<span class="legend-swatch" style="background: ${p.color}"></span> ${p.name}`;
        legend.appendChild(item);
    });
}

function drawChart(svgId, series, pools, opts) {
    const svg = document.getElementById(svgId);
    const W = 800, H = 300, PAD_L = 60, PAD_B = 28, PAD_T = 12;
    svg.innerHTML = "";
    if (!series || !series.length) return;

    const { getY, fmt = fmtUsd, yLabel } = opts;
    const colorByName = {};
    pools.forEach(p => { colorByName[p.name] = p.color; });

    const maxV = Math.max(...series.map((s, i) => s.pools.reduce((a, sp, pi) => a + getY(sp, pi, i), 0)));
    const xMax = series[series.length - 1].step;
    const x = i => PAD_L + (series[i].step / xMax) * (W - PAD_L - 10);
    const y = v => H - PAD_B - (v / (maxV || 1)) * (H - PAD_B - PAD_T);

    // Y gridlines.
    for (let g = 0; g <= 4; g++) {
        const val = maxV * g / 4;
        const yy = y(val);
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", PAD_L); line.setAttribute("x2", W - 10);
        line.setAttribute("y1", yy); line.setAttribute("y2", yy);
        line.setAttribute("stroke", "rgba(255,255,255,0.08)");
        line.setAttribute("stroke-width", "1");
        svg.appendChild(line);
        const txt = document.createElementNS("http://www.w3.org/2000/svg", "text");
        txt.setAttribute("x", PAD_L - 8); txt.setAttribute("y", yy + 4);
        txt.setAttribute("text-anchor", "end"); txt.setAttribute("font-size", "11");
        txt.setAttribute("fill", "#a3b3c9");
        txt.textContent = fmt(val);
        svg.appendChild(txt);
    }
    // X axis label.
    const xlab = document.createElementNS("http://www.w3.org/2000/svg", "text");
    xlab.setAttribute("x", (PAD_L + W) / 2); xlab.setAttribute("y", H - 6);
    xlab.setAttribute("text-anchor", "middle"); xlab.setAttribute("font-size", "11");
    xlab.setAttribute("fill", "#a3b3c9");
    xlab.textContent = yLabel || "swaps processed";
    svg.appendChild(xlab);

    pools.forEach((p, pi) => {
        const points = series.map((s, i) => {
            const sp = s.pools[pi];
            return `${x(i)},${y(getY(sp, pi, i))}`;
        });
        const poly = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        poly.setAttribute("points", points.join(" "));
        poly.setAttribute("fill", "none");
        poly.setAttribute("stroke", p.color);
        poly.setAttribute("stroke-width", "2.5");
        poly.setAttribute("stroke-linejoin", "round");
        svg.appendChild(poly);
    });
}

async function runArena() {
    const status = document.getElementById("arena-status");
    const btn = document.getElementById("run-btn");
    const pools = readPoolRows();
    if (pools.length < 2) {
        alert("Define at least 2 pools");
        return;
    }
    btn.disabled = true;
    status.textContent = "Running…";
    const body = {
        pools: pools.map(p => ({
            name: p.name,
            liquidity_usd: p.liquidity,
            range_pct: p.range,
            fee_bps: Math.round(p.feePct * 100),
        })),
        swaps: {
            count: parseInt(document.getElementById("swap-count").value, 10) || 2000,
            seed: parseInt(document.getElementById("swap-seed").value, 10) || 7,
            vol_min: parseFloat(document.getElementById("vol-min").value) || 50,
            vol_max: parseFloat(document.getElementById("vol-max").value) || 50000,
            direction_bias: parseDecimal(document.getElementById("direction-bias").value, 0.5),
        },
        days: parseFloat(document.getElementById("days").value) || 30,
    };
    try {
        const resp = await fetch("/api/pool-arena/simulate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || ("HTTP " + resp.status));
        }
        renderResults(data, pools);
        status.textContent = `Done — ${data.swap_count} swaps, total volume ${fmtUsd(data.total_volume)} over ${data.days} days`;
    } catch (err) {
        status.textContent = "";
        document.getElementById("results-section").classList.remove("hidden");
        const tbody = document.getElementById("arena-tbody");
        tbody.innerHTML = `<tr><td colspan="8" class="arena-error">${err.message}</td></tr>`;
        console.error(err);
    } finally {
        btn.disabled = false;
    }
}

function renderResults(data, pools) {
    const tbody = document.getElementById("arena-tbody");
    tbody.innerHTML = "";
    data.pools.forEach((p, i) => {
        const tr = document.createElement("tr");
        const color = pools[i] && pools[i].color ? pools[i].color : colorFor(i);
        tr.innerHTML = `
            <td><span class="legend-swatch" style="background: ${color}"></span> ${p.name}</td>
            <td>${fmtUsd(p.liquidity_usd)}</td>
            <td>${(p.fee_bps / 100).toFixed(3)}%</td>
            <td>±${p.range_pct}%</td>
            <td>${fmtUsd(p.volume)}</td>
            <td>${p.pct.toFixed(1)}%</td>
            <td>${fmtUsd(p.fee_usd)}</td>
            <td><b>${p.apr_pct.toFixed(2)}%</b></td>
        `;
        tbody.appendChild(tr);
    });

    // Sort pools by captured volume for a dominance ranking line.
    const sorted = data.pools.slice().sort((a, b) => b.volume - a.volume);
    const totalVol = data.pools.reduce((a, p) => a + p.volume, 0);
    const summary = document.getElementById("arena-summary");
    summary.innerHTML = `
        <span><b>Winner:</b> ${sorted[0].name} (${(100 * sorted[0].volume / (totalVol || 1)).toFixed(1)}% of volume)</span>
        <span><b>Total fees:</b> ${fmtUsd(data.pools.reduce((a, p) => a + p.fee_usd, 0))}</span>
        <span><b>Largest APR:</b> ${sorted[0].name} at ${sorted[0].apr_pct.toFixed(2)}%</span>
    `;

    const days = data.days || 30;
    drawChart("arena-chart", data.series, pools, {
        getY: (sp) => sp.usd + sp.reverse_usd,
        yLabel: "swaps processed",
    });
    drawChart("arena-fee-chart", data.series, pools, {
        getY: (sp) => sp.fee_usd + sp.reverse_fee_usd,
        yLabel: "swaps processed",
    });
    // Trailing-window annualized APR: fees accrued over a rolling window of
    // snapshots, normalized by the time that window spans, so it fluctuates
    // with recent fee bursts instead of monotonically climbing.
    const n = data.series.length;
    const win = Math.max(1, Math.round(n / 15));
    const apr = data.series.map((s, i) => {
        const j = Math.max(0, i - win);
        const dDays = (s.step - data.series[j].step) / (data.swap_count || 1) * days;
        return s.pools.map((sp, pi) => {
            const f0 = sp.fee_usd + sp.reverse_fee_usd;
            const f1 = data.series[j].pools[pi].fee_usd + data.series[j].pools[pi].reverse_fee_usd;
            const liq = pools[pi].liquidity || 1;
            return (f0 - f1) / liq * (365 / Math.max(dDays, 1e-9)) * 100;
        });
    });
    drawChart("arena-apr-chart", data.series, pools, {
        getY: (sp, pi, i) => apr[i][pi],
        fmt: v => v.toFixed(0) + "%",
        yLabel: "swaps processed",
    });
    document.getElementById("results-section").classList.remove("hidden");
}

document.addEventListener("DOMContentLoaded", () => {
    poolRows = DEFAULT_POOLS.map(p => ({ ...p }));
    renderPoolRows();
    document.getElementById("add-pool-btn").addEventListener("click", () => {
        poolRows.push({
            name: "Pool " + (poolRows.length + 1),
            liquidity: 100000,
            range: 10,
            feePct: 0.3,
        });
        renderPoolRows();
    });
    document.getElementById("pool-list").addEventListener("input", updateLegend);
    document.getElementById("run-btn").addEventListener("click", runArena);

    document.querySelectorAll(".arena-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".arena-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            document.querySelectorAll(".arena-chart-pane").forEach(p => p.classList.add("hidden"));
            const pane = document.getElementById("pane-" + tab.dataset.chart);
            if (pane) pane.classList.remove("hidden");
        });
    });
});
