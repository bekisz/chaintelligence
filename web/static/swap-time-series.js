(function () {
    "use strict";

    const chartSvg = document.getElementById("ts-chart");
    const tooltipEl = document.getElementById("ts-tooltip");
    const legendEl = document.getElementById("ts-legend");
    const totalEl = document.getElementById("ts-total");
    const resultsSec = document.getElementById("ts-results-section");
    const loaderEl = document.getElementById("ts-loader");
    const noDataMsg = document.getElementById("ts-no-data");

    const ctrlAxis = document.getElementById("ctrl-ts-axis");
    const ctrlInterval = document.getElementById("ctrl-ts-interval");
    const ctrlGroup = document.getElementById("ctrl-ts-group");
    const startTokenInput = document.getElementById("start-token");
    const endTokenInput = document.getElementById("end-token");
    const startDateInput = document.getElementById("start-date");
    const endDateInput = document.getElementById("end-date");
    const queryNetworkSelect = document.getElementById("query-network-filter");

    let lastData = null;
    let excludedGroups = new Set();
    let yAxisMode = "volume"; // volume | fees | count
    let groupByMode = "chain";
    let intervalMode = "day";

    const COLOR_PALETTE = [
        "#ec4899", "#8b5cf6", "#3b82f6", "#10b981", "#f59e0b",
        "#06b6d4", "#84cc16", "#e11d48", "#a855f7", "#6366f1",
        "#14b8a6", "#f97316", "#64748b"
    ];

    const GROUP_COLORS = {
        "Ethereum": "#3b82f6",
        "Arbitrum": "#28a0f0",
        "Base": "#0052ff",
        "BNB": "#f0b90b",
        "Polygon": "#8247e5",
        "Avalanche": "#e84142",
        "Optimism": "#ff0420",
        "Split": "#ec4899",
        "Non-split": "#10b981",
        "forward": "#10b981",
        "reverse": "#ec4899",
        "both": "#8b5cf6"
    };

    function getColor(groupName, idx) {
        if (GROUP_COLORS[groupName]) return GROUP_COLORS[groupName];
        return COLOR_PALETTE[idx % COLOR_PALETTE.length];
    }

    function formatNumber(num) {
        if (num >= 1e9) return (num / 1e9).toFixed(2) + "B";
        if (num >= 1e6) return (num / 1e6).toFixed(2) + "M";
        if (num >= 1e3) return (num / 1e3).toFixed(1) + "K";
        return num.toFixed(2);
    }

    function formatMetricValue(val, mode) {
        if (mode === "volume" || mode === "fees") {
            return "$" + formatNumber(val);
        }
        return val.toLocaleString();
    }

    function formatDateLabel(tsStr, interval) {
        if (!tsStr) return "";
        if (interval === "hour") {
            const parts = tsStr.split(" ");
            const dParts = parts[0].split("-");
            const dateObj = new Date(parseInt(dParts[0]), parseInt(dParts[1]) - 1, parseInt(dParts[2]));
            const monthStr = dateObj.toLocaleString("en-US", { month: "short" });
            return `${monthStr} ${dParts[2]} ${parts[1] || ''}`;
        } else {
            const dParts = tsStr.split("-");
            const dateObj = new Date(parseInt(dParts[0]), parseInt(dParts[1]) - 1, parseInt(dParts[2]));
            const monthStr = dateObj.toLocaleString("en-US", { month: "short" });
            return `${monthStr} ${dParts[2]}`;
        }
    }

    function formatLegendLabel(fullName) {
        if (!fullName) return "";
        if (groupByMode === "route") {
            if (fullName === "Others") return "Others";
            const parts = fullName.split(/\s+[\d\.\%]+\|[^\|]+\|[^\s]+\s+/);
            if (parts.length > 1) {
                return parts.join(" ➔ ");
            }
            const tokens = fullName.split(/\s+/).filter(s => !s.includes("|"));
            if (tokens.length >= 2) {
                return tokens[0] + " ➔ " + tokens[tokens.length - 1];
            }
            return fullName;
        }
        if (groupByMode === "direction") {
            const startTok = (startTokenInput?.value || "START").trim().toUpperCase();
            const endTok = (endTokenInput?.value || "END").trim().toUpperCase();
            if (fullName === "forward") return `${startTok} ➔ ${endTok}`;
            if (fullName === "reverse") return `${endTok} ➔ ${startTok}`;
        }
        return fullName;
    }

    function parseRouteTokensAndHops(rawName) {
        if (!rawName || typeof rawName !== "string") return null;
        if (rawName.includes(" -- ") && rawName.includes(" --> ")) {
            const segments = rawName.split(" --> ");
            const tokens = [];
            const hops = [];
            for (let i = 0; i < segments.length; i++) {
                const seg = segments[i].trim();
                if (seg.includes(" -- ")) {
                    const parts = seg.split(" -- ");
                    tokens.push(parts[0].trim());
                    if (i < segments.length - 1) {
                        hops.push(parts[1].trim());
                    }
                } else {
                    tokens.push(seg);
                }
            }
            if (hops.length > 0 && tokens.length > hops.length + 1) {
                tokens.splice(hops.length + 1);
            }
            return { tokens, hops };
        }
        
        const parts = rawName.split(/\s+/);
        const tokens = [];
        const hops = [];
        parts.forEach(p => {
            if (p.includes("|")) {
                hops.push(p);
            } else if (p.trim()) {
                tokens.push(p.trim());
            }
        });

        if (hops.length > 0 && tokens.length > hops.length + 1) {
            tokens.splice(hops.length + 1);
        }

        if (tokens.length >= 2 && hops.length >= 1) {
            return { tokens, hops };
        }
        return null;
    }

    function getToolTipTokenIcon(symbol) {
        if (!symbol) return '';
        const sym = symbol.toUpperCase().trim();
        let url = (window.tokenImageMap && window.tokenImageMap[sym]);
        if (!url) {
            if (['WETH', 'ETH', 'STETH', 'WSTETH', 'RETH', 'CBETH', 'WEETH', 'EZETH'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/info/logo.png';
            } else if (['USDC', 'USDC.E', 'USDCE'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/A0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo.png';
            } else if (['USDT'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/dAC17F958D2ee523a2206206994597C13D831ec7/logo.png';
            } else if (['WBTC', 'BTCB', 'BTC', 'CBBTC', 'TBTC', 'KBTC', 'LBTC', 'FBTC'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599/logo.png';
            } else if (['DAI', 'SDAI'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/6B175474E89094C44Da98b954EedeAC495271d0F/logo.png';
            } else if (['AAVE'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9/logo.png';
            } else if (['BNB', 'WBNB'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/binance/info/logo.png';
            } else if (['MATIC', 'POL', 'WMATIC'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/polygon/info/logo.png';
            } else if (['SOL', 'WSOL'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/solana/info/logo.png';
            } else if (['AVAX', 'WAVAX'].includes(sym)) {
                url = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/avalanchex/info/logo.png';
            } else {
                const s = sym.toLowerCase().replace(/^(w|st|ez|cb|we)/, '');
                url = `https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25fa5/128/color/${s}.png`;
            }
        }
        return `<img src="${url}" width="14" height="14" onerror="this.onerror=null;this.src='/static/favicon.png'" style="border-radius: 50%; vertical-align: middle; flex-shrink: 0; margin-right: 4px; display: inline-block;">`;
    }

    function formatRouteTooltipHeaderHtml(groupName, color) {
        const routeObj = parseRouteTokensAndHops(groupName);
        if (!routeObj) {
            const dispLabel = formatLegendLabel(groupName);
            return `
                <div class="tt-header">
                    <div style="display:flex; align-items:center; gap:6px;">
                        <span class="tt-swatch" style="background:${color};"></span>
                        <span style="font-weight:700;">${dispLabel}</span>
                    </div>
                </div>
            `;
        }

        const { tokens, hops } = routeObj;
        const fwdHeadSvg = `<svg class="arrow-head" viewBox="0 0 8 14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="1,1 7,7 1,13"/></svg>`;

        let pathHtml = `<div class="tt-route-header">`;
        pathHtml += `<div class="route-path-container tt-path-container">`;

        tokens.forEach((token, idx) => {
            pathHtml += `<span class="token-badge">${getToolTipTokenIcon(token)} ${token}</span>`;
            if (idx < tokens.length - 1 && idx < hops.length) {
                const hopStr = hops[idx];
                const hParts = hopStr.split('|');
                let feeStr = hParts[0] || '0.05%';
                let protoName = hParts[1] || 'Uniswap V3';

                let protoClass = 'v3';
                const rawProto = protoName.toLowerCase();
                if (rawProto.includes('v4')) protoClass = 'v4';
                else if (rawProto.includes('pancake')) protoClass = 'pancake';
                else if (!rawProto.includes('v3') && !rawProto.includes('uniswap')) protoClass = rawProto.replace(/\s+/g, '-');

                pathHtml += `
                    <div class="route-hop ${protoClass}">
                        <div class="route-hop-arrow ${protoClass}">
                            <div class="arrow-line">
                                <div class="route-hop-label">
                                    <div class="label-pane fee-pane">
                                        <span class="fee-pill">${feeStr}</span>
                                    </div>
                                </div>
                            </div>
                            ${fwdHeadSvg}
                        </div>
                    </div>
                `;
            }
        });

        pathHtml += `</div></div>`;
        return pathHtml;
    }

    function arrowDirection() {
        const d = window.routeDirection || "forward";
        return (d === "both" || d === "direct_both") ? "both" : "forward";
    }

    function updateSwapTimeSeriesGroupOptions() {
        if (!ctrlGroup) return;
        const hopsOpt = ctrlGroup.querySelector('option[value="hops"]');
        const d = window.routeDirection || "forward";
        const directCb = document.getElementById("direct-only-filter");
        const isDirectMode = d === "direct_forward" || d === "direct_both" || (directCb && directCb.checked);

        if (hopsOpt) {
            if (isDirectMode) {
                hopsOpt.hidden = true;
                hopsOpt.disabled = true;
                if (ctrlGroup.value === "hops") {
                    ctrlGroup.value = "chain";
                    groupByMode = "chain";
                }
            } else {
                hopsOpt.hidden = false;
                hopsOpt.disabled = false;
            }
        }
    }
    window.updateSwapTimeSeriesGroupOptions = updateSwapTimeSeriesGroupOptions;

    async function fetchSwapTimeSeries() {
        const startToken = startTokenInput?.value?.trim();
        const endToken = endTokenInput?.value?.trim();
        if (!startToken || !endToken) return;

        const startDate = startDateInput?.value;
        const endDate = endDateInput?.value;
        const selectedNetwork = queryNetworkSelect?.value || "all";
        const directionFilter = arrowDirection();

        updateSwapTimeSeriesGroupOptions();

        noDataMsg.classList.add("hidden");
        resultsSec.classList.add("hidden");
        loaderEl.classList.remove("hidden");

        try {
            let url = `/api/swap-time-series?start_token=${encodeURIComponent(startToken)}&end_token=${encodeURIComponent(endToken)}`;
            if (startDate) url += `&start_date=${startDate}`;
            if (endDate) url += `&end_date=${endDate}`;
            if (selectedNetwork && selectedNetwork !== "all") {
                url += `&network=${encodeURIComponent(selectedNetwork)}`;
            }
            url += `&direction=${directionFilter}`;
            url += `&group_by=${groupByMode}`;
            url += `&interval=${intervalMode}`;

            const isDirect = (window.routeDirection === "direct_forward" || window.routeDirection === "direct_both" || document.getElementById("direct-only-filter")?.checked);
            if (isDirect) {
                url += `&max_hops=1`;
            }

            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`API request failed with status ${response.status}`);
            }
            const payload = await response.json();
            const d = payload.data;

            if (!d || !d.timestamps || d.timestamps.length === 0) {
                loaderEl.classList.add("hidden");
                noDataMsg.classList.remove("hidden");
                return;
            }

            lastData = d;
            excludedGroups.clear();
            loaderEl.classList.add("hidden");
            resultsSec.classList.remove("hidden");

            renderTimeSeriesChart();
        } catch (err) {
            console.error("[swap-time-series] fetch failed:", err);
            loaderEl.classList.add("hidden");
            noDataMsg.classList.remove("hidden");
        }
    }

    function renderTimeSeriesChart() {
        if (!lastData || !chartSvg) return;

        const d = lastData;
        const timestamps = d.timestamps;
        const groups = d.groups || [];
        const series = d.series || {};
        const interval = d.interval || "day";

        // Active groups (filtering out excludedGroups)
        const activeGroups = groups.filter(g => !excludedGroups.has(g));

        // SVG bounds
        const svgWidth = 900;
        const svgHeight = 480;
        const margin = { top: 35, right: 30, bottom: 65, left: 75 };
        const width = svgWidth - margin.left - margin.right;
        const height = svgHeight - margin.top - margin.bottom;

        // Compute total values per timestamp bucket for active groups
        const numBuckets = timestamps.length;
        const bucketTotals = new Array(numBuckets).fill(0);
        const bucketStacks = new Array(numBuckets).fill(null).map(() => []);

        numBuckets && timestamps.forEach((ts, bIdx) => {
            let runningSum = 0;
            activeGroups.forEach((groupName, gIdx) => {
                const arr = series[groupName]?.[yAxisMode] || [];
                const val = arr[bIdx] || 0;
                const y0 = runningSum;
                const y1 = runningSum + val;
                runningSum = y1;
                bucketStacks[bIdx].push({
                    groupName,
                    val,
                    y0,
                    y1,
                    color: getColor(groupName, groups.indexOf(groupName))
                });
            });
            bucketTotals[bIdx] = runningSum;
        });

        const maxVal = Math.max(...bucketTotals, 1);
        const grandTotal = bucketTotals.reduce((a, b) => a + b, 0);

        if (totalEl) {
            totalEl.textContent = formatMetricValue(grandTotal, yAxisMode);
        }

        // Render SVG elements
        let svgHtml = ``;

        // 1. Gridlines & Y-Axis Ticks
        const numYTicks = 5;
        for (let i = 0; i <= numYTicks; i++) {
            const tickVal = (maxVal / numYTicks) * i;
            const yPos = margin.top + height - (height * (tickVal / maxVal));

            // Gridline
            svgHtml += `<line x1="${margin.left}" y1="${yPos}" x2="${margin.left + width}" y2="${yPos}" stroke="rgba(255,255,255,0.07)" stroke-dasharray="3,3" />`;

            // Y-Axis label
            svgHtml += `<text x="${margin.left - 10}" y="${yPos + 4}" fill="rgba(255,255,255,0.5)" font-size="11" text-anchor="end" font-family="Inter, sans-serif">${formatMetricValue(tickVal, yAxisMode)}</text>`;
        }

        // 2. Linear X-Axis & Stacked Bars
        const barGap = numBuckets > 30 ? 2 : 6;
        const totalBarSpace = width / numBuckets;
        const barWidth = Math.max(1, totalBarSpace - barGap);

        // X-axis line
        svgHtml += `<line x1="${margin.left}" y1="${margin.top + height}" x2="${margin.left + width}" y2="${margin.top + height}" stroke="rgba(255,255,255,0.2)" stroke-width="1.5" />`;

        // X-axis Ticks
        const labelInterval = Math.ceil(numBuckets / 10);

        timestamps.forEach((tsStr, bIdx) => {
            const xCenter = margin.left + (bIdx + 0.5) * totalBarSpace;
            const xBarLeft = xCenter - barWidth / 2;

            // Render Stacked Bar segments
            const stack = bucketStacks[bIdx];
            stack.forEach(seg => {
                if (seg.val <= 0) return;
                const yTop = margin.top + height - (height * (seg.y1 / maxVal));
                const segHeight = Math.max(1, (height * (seg.val / maxVal)));

                svgHtml += `<rect x="${xBarLeft}" y="${yTop}" width="${barWidth}" height="${segHeight}" fill="${seg.color}" rx="1.5" opacity="0.85" class="ts-bar-seg" data-bidx="${bIdx}" data-group="${seg.groupName}" data-val="${seg.val}" data-y0="${seg.y0}" data-y1="${seg.y1}" style="transition: opacity 0.15s ease, filter 0.15s ease; cursor: pointer;" />`;
            });

            // X-axis tick label
            if (bIdx % labelInterval === 0 || bIdx === numBuckets - 1) {
                const labelStr = formatDateLabel(tsStr, interval);
                svgHtml += `<text x="${xCenter}" y="${margin.top + height + 20}" fill="rgba(255,255,255,0.6)" font-size="11" text-anchor="middle" font-family="Inter, sans-serif">${labelStr}</text>`;
                svgHtml += `<line x1="${xCenter}" y1="${margin.top + height}" x2="${xCenter}" y2="${margin.top + height + 5}" stroke="rgba(255,255,255,0.3)" />`;
            }
        });

        chartSvg.innerHTML = svgHtml;

        // Render Legend
        renderLegend(groups);

        // Attach Segment Hover & Tooltip listeners
        attachSegmentHoverListeners(timestamps, bucketTotals);
    }

    function renderLegend(groups) {
        if (!legendEl) return;
        let legendHtml = ``;

        groups.forEach((gName, idx) => {
            const color = getColor(gName, idx);
            const isExcluded = excludedGroups.has(gName);
            const dispLabel = formatLegendLabel(gName);
            legendHtml += `
                <button type="button" class="chain-toggle ${isExcluded ? 'chain-off' : ''}" data-group="${gName}" title="Toggle ${dispLabel}">
                    <span class="swatch" style="background:${color};"></span>
                    <span>${dispLabel}</span>
                </button>
            `;
        });

        legendEl.innerHTML = legendHtml;

        legendEl.querySelectorAll(".chain-toggle").forEach(btn => {
            const groupName = btn.dataset.group;

            btn.addEventListener("mouseenter", () => {
                highlightGroup(groupName);
            });

            btn.addEventListener("mouseleave", () => {
                clearHighlight();
            });

            btn.addEventListener("click", () => {
                if (excludedGroups.has(groupName)) {
                    excludedGroups.delete(groupName);
                } else {
                    const visible = groups.filter(g => !excludedGroups.has(g));
                    if (visible.length <= 1) return;
                    excludedGroups.add(groupName);
                }
                renderTimeSeriesChart();
            });
        });
    }

    function highlightGroup(groupName) {
        if (!chartSvg) return;
        chartSvg.querySelectorAll(".ts-bar-seg").forEach(seg => {
            if (seg.dataset.group === groupName) {
                seg.style.opacity = "1";
                seg.style.filter = "brightness(1.15)";
            } else {
                seg.style.opacity = "0.2";
                seg.style.filter = "none";
            }
        });

        if (legendEl) {
            legendEl.querySelectorAll(".chain-toggle").forEach(btn => {
                if (btn.dataset.group === groupName) {
                    btn.classList.add("chain-highlight");
                    btn.classList.remove("chain-dimmed");
                } else {
                    btn.classList.remove("chain-highlight");
                    btn.classList.add("chain-dimmed");
                }
            });
        }
    }

    function clearHighlight() {
        if (!chartSvg) return;
        chartSvg.querySelectorAll(".ts-bar-seg").forEach(seg => {
            seg.style.opacity = "0.85";
            seg.style.filter = "none";
        });

        if (legendEl) {
            legendEl.querySelectorAll(".chain-toggle").forEach(btn => {
                btn.classList.remove("chain-highlight");
                btn.classList.remove("chain-dimmed");
            });
        }
    }

    function attachSegmentHoverListeners(timestamps, bucketTotals) {
        const segs = chartSvg.querySelectorAll(".ts-bar-seg");
        segs.forEach(seg => {
            seg.addEventListener("mousemove", (e) => {
                const bIdx = parseInt(seg.dataset.bidx);
                const groupName = seg.dataset.group;
                const segVal = parseFloat(seg.dataset.val || "0");
                const bTotal = bucketTotals[bIdx] || 0;
                const pct = bTotal > 0 ? (segVal / bTotal * 100) : 0;
                const tsStr = timestamps[bIdx];

                // Highlight this segment's group in chart & legend
                highlightGroup(groupName);

                // Build segment-specific tooltip matching Swap Distribution
                const color = getColor(groupName, (lastData?.groups || []).indexOf(groupName));
                const headerHtml = formatRouteTooltipHeaderHtml(groupName, color);
                const metricName = yAxisMode === "fees" ? "Fees" : yAxisMode === "count" ? "Swaps" : "USD Volume";

                tooltipEl.innerHTML = `
                    ${headerHtml}
                    <div class="tt-title">${formatDateLabel(tsStr, lastData?.interval)}</div>
                    <div class="tt-row">
                        <span>${metricName}</span>
                        <b>${formatMetricValue(segVal, yAxisMode)} <em style="color:#9fb0c6; font-size:0.75rem;">(${pct.toFixed(1)}%)</em></b>
                    </div>
                    <div class="tt-row" style="margin-top:6px; padding-top:4px; border-top:1px solid rgba(255,255,255,0.1); color:#a3b3c9;">
                        <span>Bucket Total</span>
                        <b>${formatMetricValue(bTotal, yAxisMode)}</b>
                    </div>
                `;

                tooltipEl.classList.add("visible");

                // Position tooltip
                const chartBox = chartSvg.parentElement;
                const rect = chartBox.getBoundingClientRect();
                let left = e.clientX - rect.left + 15;
                let top = e.clientY - rect.top - 70;

                if (left + 240 > rect.width) left = rect.width - 250;
                if (left < 10) left = 10;
                if (top < 10) top = e.clientY - rect.top + 15;

                tooltipEl.style.left = `${left}px`;
                tooltipEl.style.top = `${top}px`;
            });

            seg.addEventListener("mouseleave", () => {
                clearHighlight();
                tooltipEl.classList.remove("visible");
            });
        });
    }

    // Attach Controls Listeners
    if (ctrlAxis) {
        ctrlAxis.addEventListener("change", () => {
            yAxisMode = ctrlAxis.value;
            renderTimeSeriesChart();
        });
    }

    if (ctrlInterval) {
        ctrlInterval.addEventListener("change", () => {
            intervalMode = ctrlInterval.value;
            fetchSwapTimeSeries();
        });
    }

    if (ctrlGroup) {
        ctrlGroup.addEventListener("change", () => {
            groupByMode = ctrlGroup.value;
            fetchSwapTimeSeries();
        });
    }

    // Export function to window
    window.fetchSwapTimeSeries = fetchSwapTimeSeries;
})();
