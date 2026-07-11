/**
 * SPS — Stable-Pair Shortcut Finder UI
 *
 * Calls /api/sps/find and renders shortcut opportunities as expandable cards.
 */
document.addEventListener('DOMContentLoaded', async () => {
    const analyzeBtn = document.getElementById('sps-analyze-btn');
    const startDateInput = document.getElementById('sps-start-date');
    const endDateInput = document.getElementById('sps-end-date');
    const familiesInput = document.getElementById('sps-families');
    const crossFamilyInput = document.getElementById('sps-cross-family');
    const minVolumeInput = document.getElementById('sps-min-volume');
    const tvlTargetsInput = document.getElementById('sps-tvl-targets');
    const resultsSection = document.getElementById('sps-results');
    const opportunitiesContainer = document.getElementById('sps-opportunities');
    const loader = document.getElementById('sps-loader');
    const noDataMsg = document.getElementById('sps-no-data');

    // Stats elements
    const statCount = document.getElementById('sps-stat-count');
    const statVolume = document.getElementById('sps-stat-volume');
    const statTxns = document.getElementById('sps-stat-txns');
    const statRevenue = document.getElementById('sps-stat-revenue');

    // Fetch available date range
    try {
        const resp = await fetch('/api/routes/date-range');
        const dateRange = await resp.json();
        if (dateRange.min_date && dateRange.max_date) {
            startDateInput.min = dateRange.min_date;
            startDateInput.max = dateRange.max_date;
            endDateInput.min = dateRange.min_date;
            endDateInput.max = dateRange.max_date;

            const today = new Date().toISOString().split('T')[0];
            const maxDate = dateRange.max_date;
            endDateInput.value = today <= maxDate ? today : maxDate;

            const endDate = new Date(endDateInput.value);
            const thirtyDaysAgo = new Date(endDate);
            thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
            const agoStr = thirtyDaysAgo.toISOString().split('T')[0];
            startDateInput.value = agoStr >= dateRange.min_date ? agoStr : dateRange.min_date;
        }
    } catch (e) {
        const today = new Date().toISOString().split('T')[0];
        endDateInput.value = today;
        const ago = new Date();
        ago.setDate(ago.getDate() - 30);
        startDateInput.value = ago.toISOString().split('T')[0];
    }

    // Helpers
    const formatUSD = (amount) => {
        const digits = amount >= 10 ? 0 : 2;
        return new Intl.NumberFormat('en-US', {
            style: 'currency', currency: 'USD',
            minimumFractionDigits: digits, maximumFractionDigits: digits
        }).format(amount);
    };

    const formatPct = (val) => {
        if (val === null || val === undefined) return 'N/A';
        return (val * 100).toFixed(2) + '%';
    };

    const formatPctRaw = (val) => {
        if (val === null || val === undefined) return 'N/A';
        return val.toFixed(3) + '%';
    };

    // Main analysis
    const performAnalysis = async () => {
        loader.classList.remove('hidden');
        resultsSection.classList.add('hidden');
        noDataMsg.classList.add('hidden');

        // Elapsed time counter
        const loaderDetail = document.querySelector('.sps-loader-detail');
        const startTime = Date.now();
        const timerInterval = setInterval(() => {
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            if (loaderDetail) loaderDetail.textContent = `Elapsed: ${elapsed}s — scanning routes across all families...`;
        }, 1000);

        try {
            const params = new URLSearchParams();
            params.set('start_date', startDateInput.value);
            params.set('end_date', endDateInput.value);
            params.set('min_volume', minVolumeInput.value || '10000');
            params.set('cross_family', crossFamilyInput.checked ? 'true' : 'false');

            const families = familiesInput.value.trim();
            if (families) {
                params.set('families', families);
            }

            const tvlTargets = tvlTargetsInput.value.trim();
            if (tvlTargets) {
                params.set('tvl_targets', tvlTargets);
            }

            // 5-minute timeout for large date ranges
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 300000);

            const response = await fetch(`/api/sps/find?${params.toString()}`, {
                signal: controller.signal
            });
            clearTimeout(timeoutId);
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'API request failed');
            }

            const data = await response.json();
            const opportunities = data.opportunities || [];

            if (opportunities.length === 0) {
                noDataMsg.classList.remove('hidden');
                loader.classList.add('hidden');
                return;
            }

            // Update summary stats
            const totalVolume = opportunities.reduce((s, o) => s + o.divertable_volume, 0);
            const totalTxns = opportunities.reduce((s, o) => s + o.divertable_txns, 0);

            // Sum daily revenue at the smallest TVL target across opportunities
            const totalDailyRev = opportunities.reduce((s, o) => {
                const firstProj = o.projections && o.projections.length > 0 ? o.projections[0] : null;
                return s + (firstProj ? firstProj.daily_revenue : 0);
            }, 0);

            statCount.textContent = opportunities.length;
            statVolume.textContent = formatUSD(totalVolume);
            statTxns.textContent = totalTxns.toLocaleString();
            statRevenue.textContent = formatUSD(totalDailyRev) + '/day';

            // Render opportunities
            renderOpportunities(opportunities, data.period);

            resultsSection.classList.remove('hidden');
        } catch (error) {
            console.error('SPS Analysis Error:', error);
            noDataMsg.innerHTML = `<p>Analysis failed: ${error.message}</p>`;
            noDataMsg.classList.remove('hidden');
        } finally {
            clearInterval(timerInterval);
            loader.classList.add('hidden');
        }
    };

    const renderOpportunities = (opportunities, period) => {
        opportunitiesContainer.innerHTML = '';

        opportunities.forEach((opp, idx) => {
            const card = document.createElement('div');
            card.className = 'sps-card glass';
            card.innerHTML = buildCardHTML(opp, idx + 1, period);
            opportunitiesContainer.appendChild(card);

            // Toggle expand
            const header = card.querySelector('.sps-card-header');
            const body = card.querySelector('.sps-card-body');
            header.addEventListener('click', () => {
                body.classList.toggle('hidden');
                header.classList.toggle('expanded');
            });
        });
    };

    const buildCardHTML = (opp, rank, period) => {
        const days = period ? period.days : 30;
        const dailyVol = opp.daily_volume || 0;
        const feeSaving = opp.fee_saving_pct || 0;

        // Pool status
        let poolStatus = '';
        if (opp.existing_pool && opp.existing_pool.exists) {
            if (opp.existing_pool.tvl && opp.existing_pool.tvl > 0) {
                poolStatus = `<span class="sps-badge sps-badge-exists">⚡ Pool exists (TVL: ${formatUSD(opp.existing_pool.tvl)})</span>`;
            } else {
                poolStatus = `<span class="sps-badge sps-badge-exists">⚡ Pool exists (fee: ${opp.existing_pool.fee_tier})</span>`;
            }
        } else {
            poolStatus = `<span class="sps-badge sps-badge-new">🆕 No direct pool</span>`;
        }

        // Family label
        const famLabel = opp.is_cross_family
            ? `<span class="sps-family-tag cross">${opp.family_a}×${opp.family_b}</span>`
            : `<span class="sps-family-tag">${opp.family_a}</span>`;

        // Dominant route visualization
        let routeViz = '';
        if (opp.dominant_route && opp.dominant_route.path) {
            routeViz = renderRoutePath(opp.dominant_route.path);
        }

        // Projections table
        let projRows = '';
        if (opp.projections && opp.projections.length > 0) {
            opp.projections.forEach(p => {
                const tvlLabel = p.is_actual_tvl ? `${formatUSD(p.tvl)} <em>(actual)</em>` : formatUSD(p.tvl);
                const aprClass = p.apr > 0.10 ? 'text-success font-bold' : (p.apr > 0.03 ? 'text-success' : '');
                projRows += `
                    <tr>
                        <td>${tvlLabel}</td>
                        <td>${formatUSD(p.daily_revenue)}/day</td>
                        <td class="${aprClass}">${formatPct(p.apr)}</td>
                    </tr>
                `;
            });
        }

        // Other routes
        let otherRoutes = '';
        if (opp.multihop_routes && opp.multihop_routes.length > 1) {
            otherRoutes = '<div class="sps-other-routes"><h4>Other multi-hop routes:</h4>';
            opp.multihop_routes.slice(1, 5).forEach(r => {
                otherRoutes += `
                    <div class="sps-other-route-row">
                        <span class="sps-route-mini">${renderRoutePath(r.path)}</span>
                        <span class="sps-route-stats">${formatUSD(r.volume)} · ${r.txns} txns</span>
                    </div>
                `;
            });
            otherRoutes += '</div>';
        }

        // Existing direct route info
        let directInfo = '';
        if (opp.direct_routes && opp.direct_routes.length > 0) {
            const directVol = opp.direct_routes.reduce((s, r) => s + r.volume, 0);
            const directTxns = opp.direct_routes.reduce((s, r) => s + r.txns, 0);
            directInfo = `
                <div class="sps-direct-info">
                    ℹ️ Existing direct route volume: ${formatUSD(directVol)} (${directTxns} txns)
                </div>
            `;
        }

        return `
            <div class="sps-card-header">
                <div class="sps-card-rank">#${rank}</div>
                <div class="sps-card-title">
                    <h3>${opp.pair}</h3>
                    ${famLabel}
                    ${poolStatus}
                </div>
                <div class="sps-card-headline-stats">
                    <div class="sps-headline-stat">
                        <span class="sps-headline-value">${formatUSD(opp.divertable_volume)}</span>
                        <span class="sps-headline-label">Divertable</span>
                    </div>
                    <div class="sps-headline-stat">
                        <span class="sps-headline-value">${formatPctRaw(feeSaving)}</span>
                        <span class="sps-headline-label">Fee Saving</span>
                    </div>
                    <div class="sps-headline-stat">
                        <span class="sps-headline-value">${opp.divertable_txns.toLocaleString()}</span>
                        <span class="sps-headline-label">Transactions</span>
                    </div>
                </div>
                <div class="expand-toggle">▼</div>
            </div>

            <div class="sps-card-body hidden">
                <!-- Dominant Route -->
                <div class="sps-section">
                    <h4>Dominant Route</h4>
                    <div class="sps-dominant-route">${routeViz}</div>
                </div>

                <!-- Fee Comparison -->
                <div class="sps-section sps-fee-comparison">
                    <div class="sps-fee-bar">
                        <div class="sps-fee-item sps-fee-current">
                            <span class="sps-fee-label">Current Avg Fee</span>
                            <span class="sps-fee-value">${formatPctRaw(opp.current_cumulative_fee_pct)}</span>
                        </div>
                        <div class="sps-fee-arrow">→</div>
                        <div class="sps-fee-item sps-fee-proposed">
                            <span class="sps-fee-label">Proposed Shortcut</span>
                            <span class="sps-fee-value">${formatPctRaw(opp.proposed_shortcut_fee_pct)}</span>
                        </div>
                        <div class="sps-fee-saving">
                            <span>Trader saves ${formatPctRaw(feeSaving)} per swap</span>
                        </div>
                    </div>
                </div>

                <!-- Volume Stats -->
                <div class="sps-section sps-volume-stats">
                    <div class="sps-mini-stat">
                        <span class="sps-mini-label">Daily Volume</span>
                        <span class="sps-mini-value">${formatUSD(dailyVol)}/day</span>
                    </div>
                    <div class="sps-mini-stat">
                        <span class="sps-mini-label">Avg per TX</span>
                        <span class="sps-mini-value">${opp.divertable_txns > 0 ? formatUSD(opp.divertable_volume / opp.divertable_txns) : 'N/A'}</span>
                    </div>
                </div>

                ${directInfo}

                <!-- Revenue Projections -->
                <div class="sps-section">
                    <h4>Revenue Projections</h4>
                    <table class="sps-projection-table">
                        <thead>
                            <tr>
                                <th>TVL</th>
                                <th>Daily Revenue</th>
                                <th>APR</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${projRows}
                        </tbody>
                    </table>
                </div>

                ${otherRoutes}
            </div>
        `;
    };

    const renderRoutePath = (pathStr) => {
        if (!pathStr) return '';
        // Parse format: "USDC -- 0.05%|v3 --> WETH -- 0.3%|v3 --> DAI"
        const parts = pathStr.split(/\s+/);
        let html = '<div class="route-path-container">';
        let i = 0;
        while (i < parts.length) {
            const token = parts[i];
            if (token === '--' || token === '-->') {
                i++;
                continue;
            }
            if (token.includes('%') || token.includes('|')) {
                // Fee pill
                let cleanFee = token;
                let protocol = '';
                if (token.includes('|')) {
                    const [fee, proto] = token.split('|');
                    cleanFee = fee;
                    protocol = proto ? ` (${proto.toUpperCase()})` : '';
                }
                html += `
                    <div class="route-arrow-wrapper">
                        <span class="fee-pill">${cleanFee}${protocol}</span>
                        <svg class="route-arrow-svg" viewBox="0 0 192 24" fill="none" stroke="currentColor">
                            <path d="M5 12h182M175 5l7 7-7 7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                `;
            } else {
                // Token badge
                html += `<span class="token-badge">${token}</span>`;
            }
            i++;
        }
        html += '</div>';
        return html;
    };

    // Event listeners
    analyzeBtn.addEventListener('click', performAnalysis);

    [startDateInput, endDateInput, familiesInput, minVolumeInput].forEach(input => {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') performAnalysis();
        });
    });
});
