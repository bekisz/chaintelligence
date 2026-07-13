let tokenImageMap = {};
let tokenSlugMap = {};
let poolStatsMap = {};

const getCmcUrl = (tokenSymbol) => {
    const symbol = (tokenSymbol || '').toUpperCase().trim();
    const slug = tokenSlugMap[symbol] || symbol.toLowerCase();
    return `https://coinmarketcap.com/currencies/${slug}/`;
};

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

    // Fetch official token slugs and images from backend (non-blocking — populate map when ready)
    fetch('/api/coin/list')
        .then(response => response.json())
        .then(coins => {
            coins.forEach(coin => {
                if (coin.symbol) {
                    const upperSymbol = coin.symbol.toUpperCase();
                    if (coin.image) {
                        tokenImageMap[upperSymbol] = coin.image;
                    }
                    if (coin.slug) {
                        tokenSlugMap[upperSymbol] = coin.slug;
                    }
                }
            });
        })
        .catch(error => {
            console.error('Error fetching token list:', error);
        });

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
        return Number((val * 100).toFixed(1)) + '%';
    };

    const formatPctRaw = (val) => {
        if (val === null || val === undefined) return 'N/A';
        return Number(val.toFixed(1)) + '%';
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
            poolStatsMap = data.pool_stats || {};
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

    const parseProtocol = (feeString) => {
        let cleanFee = feeString || '';
        let protocolName = 'Uniswap';
        let protocolClass = 'v3';
        let networkName = '';

        if (feeString && feeString.includes('|')) {
            const parts = feeString.split('|');
            cleanFee = parts[0];
            if (parts[1]) {
                protocolName = parts[1].trim();
                const rawProto = parts[1].trim().toLowerCase();
                if (rawProto === 'uniswap v3' || rawProto === 'v3' || rawProto === 'uniswap-v3') {
                    protocolClass = 'v3';
                } else if (rawProto === 'uniswap v4' || rawProto === 'v4' || rawProto === 'uniswap-v4') {
                    protocolClass = 'v4';
                } else {
                    protocolClass = rawProto.replace(/\s+/g, '-');
                }
            }
            if (parts[2]) {
                networkName = parts[2].trim();
            }
        }
        return { cleanFee, protocolName, protocolClass, networkName };
    };

    const tokenIconHtml = (symbol) => {
        let sym = symbol.toUpperCase();
        if (sym === 'WBNB') sym = 'BNB';
        else if (sym === 'WETH') sym = 'ETH';
        else if (sym === 'WBTC') sym = 'BTC';
        const url = tokenImageMap[sym];
        if (url) {
            return `<img class="token-icon" src="${url}" alt="${symbol}">`;
        }
        return '';
    };

    const renderRoutePath = (pathStr) => {
        if (!pathStr) return '';
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
                const feeRawFull = token;
                const prevToken = parts[i-2] || '';
                const nextToken = parts[i+2] || '';

                const keyFwd = `${prevToken}-${nextToken}-${feeRawFull}`;
                const keyRev = `${nextToken}-${prevToken}-${feeRawFull}`;
                const stats = poolStatsMap[keyFwd] || poolStatsMap[keyRev] || { apr: 0.0, apr_str: 'N/A', pool_address: null };

                const parsed = parseProtocol(feeRawFull);
                let cleanFee = parsed.cleanFee;
                const protocolName = parsed.protocolName;
                const networkName = parsed.networkName;
                const protocolClass = parsed.protocolClass;

                let dispFee = cleanFee;
                const parsedFee = parseFloat(cleanFee);
                if (!isNaN(parsedFee) && parsedFee >= 5) {
                    dispFee = (parsedFee / 10000) + '%';
                    cleanFee = dispFee;
                }
                if (cleanFee.toLowerCase() === 'dynamic') {
                    dispFee = 'dyn';
                }
                const feeDisplay = dispFee;

                let aprDisplay = '';
                if (stats.apr !== undefined && stats.apr !== null && stats.apr >= 0) {
                    const aprVal = stats.apr * 100;
                    aprDisplay = Number(aprVal.toFixed(1)) + '%';
                }

                const arrowTooltip = `${protocolName}${networkName ? ' on ' + networkName : ''}`;

                let uniLinkHtml = '';
                let uniHref = '';
                let uniProtocol = '';
                const pool_addr = stats.pool_address;

                if (pool_addr) {
                    const protocolNameLower = protocolName.toLowerCase();
                    const networkLower = (networkName || 'ethereum').toLowerCase();

                    if (protocolNameLower.includes('uniswap v4')) {
                        let uniNetwork = 'ethereum';
                        if (networkLower.includes('base')) {
                            uniNetwork = 'base';
                        } else if (networkLower.includes('eth')) {
                            uniNetwork = 'ethereum';
                        } else if (networkLower.includes('bnb') || networkLower.includes('bsc')) {
                            uniNetwork = 'bnb';
                        } else if (networkLower.includes('arbitrum')) {
                            uniNetwork = 'arbitrum';
                        } else if (networkLower.includes('optimism')) {
                            uniNetwork = 'optimism';
                        }
                        uniHref = `https://app.uniswap.org/explore/pools/${uniNetwork}/${pool_addr}`;
                        uniProtocol = 'uniswap';
                    } else if (protocolNameLower.includes('pancake')) {
                        let pChain = 'bsc';
                        if (networkLower.includes('base')) {
                            pChain = 'base';
                        } else if (networkLower.includes('eth')) {
                            pChain = 'eth';
                        } else if (networkLower.includes('arbitrum')) {
                            pChain = 'arb';
                        }
                        if (protocolNameLower.includes('v4')) {
                            if (pool_addr.length === 66) {
                                uniHref = `https://pancakeswap.finance/liquidity/pool/${pChain}/${pool_addr}`;
                            } else {
                                uniHref = `https://pancakeswap.finance/info/infinity/pairs/tokens/${pool_addr}?chain=${pChain}`;
                            }
                        } else {
                            uniHref = `https://pancakeswap.finance/info/v3/pairs/${pool_addr}?chain=${pChain}`;
                        }
                        uniProtocol = 'pancakeswap';
                    } else if (protocolNameLower.includes('uniswap') || protocolNameLower.includes('v3') || protocolNameLower.includes('v2')) {
                        let uniNetwork = 'ethereum';
                        if (networkLower.includes('base')) {
                            uniNetwork = 'base';
                        } else if (networkLower.includes('eth')) {
                            uniNetwork = 'ethereum';
                        } else if (networkLower.includes('bnb') || networkLower.includes('bsc')) {
                            uniNetwork = 'bnb';
                        } else if (networkLower.includes('arbitrum')) {
                            uniNetwork = 'arbitrum';
                        } else if (networkLower.includes('optimism')) {
                            uniNetwork = 'optimism';
                        } else if (networkLower.includes('polygon')) {
                            uniNetwork = 'polygon';
                        }
                        uniHref = `https://app.uniswap.org/explore/pools/${uniNetwork}/${pool_addr}`;
                        uniProtocol = 'uniswap';
                    }
                }

                if (uniHref && uniProtocol) {
                    // Uniswap unicorn icon (pink) - solid silhouette
                    const uniswapIconSvg = `<svg class="proto-brand-icon" viewBox="0 0 438 504" fill="#FF007A" xmlns="http://www.w3.org/2000/svg">
                        <path d="M171.43,114.54c-5.45-.78-5.71-1-3.12-1.3,4.94-.78,16.37.26,24.42,2.08,18.7,4.41,35.58,15.84,53.5,35.84l4.68,5.46,6.75-1c28.83-4.68,58.44-1,83.11,10.39,6.76,3.11,17.41,9.35,18.7,10.9.52.52,1.3,3.9,1.82,7.28,1.82,12.2,1,21.29-2.85,28.31-2.08,3.89-2.08,4.93-.78,8.31a7.79,7.79,0,0,0,7,4.41c6.23,0,12.73-9.87,15.84-23.63l1.3-5.46,2.34,2.6c13.24,14.81,23.63,35.32,25.19,49.87l.52,3.89-2.34-3.37c-3.89-6-7.53-9.87-12.46-13.25-8.83-6-18.18-7.79-42.86-9.09-22.33-1.3-35.06-3.11-47.53-7.27-21.3-7-32.2-16.1-57.4-49.61-11.17-14.8-18.18-22.85-25.19-29.61C206.75,125.45,191.43,117.66,171.43,114.54Z"/>
                        <path d="M364.93,147.53c.52-9.87,1.82-16.37,4.67-22.34,1-2.34,2.08-4.42,2.34-4.42s-.26,1.82-1,3.9c-2.08,5.71-2.34,13.76-1,22.86,1.82,11.68,2.6,13.24,15.07,26,5.71,6,12.46,13.5,15.06,16.62l4.42,5.71L400,191.68c-5.45-5.2-17.92-15.07-20.78-16.36-1.81-1-2.07-1-3.37.26-1,1-1.3,2.59-1.3,10.12-.26,11.69-1.82,19-5.72,26.5-2.07,3.89-2.33,3.11-.52-1.3,1.3-3.38,1.56-4.94,1.56-16.1,0-22.6-2.59-28.06-18.44-37.15-3.89-2.33-10.65-5.71-14.54-7.53a57.93,57.93,0,0,1-7-3.37c.51-.52,15.84,3.89,21.81,6.49,9.09,3.64,10.65,3.89,11.69,3.64C364.15,156.1,364.67,154,364.93,147.53Z"/>
                        <path d="M182.08,186.22c-10.91-15.06-17.92-38.44-16.36-55.84l.52-5.45,2.59.52a60.93,60.93,0,0,1,16.63,6.23c10.39,6.24,15.06,14.81,19.48,36.1,1.29,6.24,3.11,13.51,3.89,15.85,1.3,3.89,6.24,13,10.39,18.7,2.86,4.15,1,6.23-5.45,5.71C203.9,207,190.65,197.91,182.08,186.22Z"/>
                        <path d="M351.68,299.21c-51.42-20.78-69.6-38.7-69.6-69.09,0-4.42.25-8.05.25-8.05a49.86,49.86,0,0,1,4.42,3.37c10.39,8.31,22.08,11.95,54.54,16.63,19,2.85,29.87,4.93,39.74,8.31,31.43,10.39,50.91,31.68,55.58,60.51,1.3,8.31.52,24.16-1.56,32.47-1.81,6.49-7,18.44-8.31,18.7-.26,0-.78-1.3-.78-3.38-.52-10.91-6-21.29-15.06-29.35C400,320,386,313,351.68,299.21Z"/>
                        <path d="M315.32,307.78a61.45,61.45,0,0,0-2.6-10.91l-1.3-3.9,2.34,2.86c3.38,3.9,6,8.57,8.31,15.06,1.82,4.94,1.82,6.5,1.82,14.55,0,7.79-.26,9.61-1.82,14a46.86,46.86,0,0,1-10.91,17.41c-9.35,9.61-21.55,14.8-39,17.14-3.12.26-11.95,1-19.74,1.56-19.48,1-32.47,3.11-44.16,7.27-1.56.52-3.11,1-3.37.78-.52-.52,7.53-5.2,14-8.31,9.09-4.42,18.44-6.76,39-10.39,10.13-1.56,20.52-3.64,23.12-4.68C306.75,352.19,319.48,332.19,315.32,307.78Z"/>
                        <path d="M339,349.59q-10.14-22.2-4.68-42.07c.52-1.3,1-2.6,1.56-2.6a11.07,11.07,0,0,1,3.63,1.82c3.12,2.08,9.61,5.71,26.24,14.8,21,11.43,33,20.26,41.29,30.39,7.28,8.83,11.69,19,13.77,31.43,1.3,7,.52,23.89-1.3,30.9-5.71,22.08-18.7,39.74-37.66,49.87a36.28,36.28,0,0,1-5.45,2.6c-.26,0,.78-2.6,2.33-5.71,6.24-13.25,7-26,2.34-40.26-2.86-8.83-8.83-19.48-20.78-37.4C346,362.58,342.59,357.12,339,349.59Z"/>
                        <path d="M145.46,429.07c19.22-16.1,42.85-27.53,64.67-31.17,9.35-1.56,24.93-1,33.51,1.3,13.76,3.64,26.23,11.43,32.72,21,6.23,9.35,9.09,17.4,11.95,35.32,1,7,2.34,14.29,2.6,15.84,2.07,9.36,6.23,16.63,11.42,20.52,8.06,6,22.08,6.24,35.85,1,2.33-.78,4.41-1.56,4.41-1.3.52.52-6.49,5.2-11.17,7.54a36.81,36.81,0,0,1-18.7,4.41c-12.46,0-23.11-6.49-31.68-19.48-1.82-2.6-5.46-10.13-8.57-17.14-9.1-21-13.77-27.27-24.42-34.28-9.35-6-21.3-7.28-30.39-2.86-11.94,5.71-15.06,21-6.75,30.39,3.38,3.89,9.61,7,14.8,7.79a15.86,15.86,0,0,0,17.93-15.85c0-6.23-2.34-9.86-8.58-12.72-8.31-3.64-17.4.52-17.14,8.57,0,3.38,1.56,5.45,4.94,7,2.08,1,2.08,1,.52.78-7.54-1.56-9.35-10.91-3.38-16.88,7.27-7.27,22.6-4.16,27.79,6,2.08,4.16,2.34,12.47.52,17.66C243.9,474,231.43,480,218.7,476.6c-8.57-2.34-12.21-4.68-22.59-15.32-18.19-18.7-25.2-22.34-51.17-26.24l-4.94-.78Z"/>
                        <path fill-rule="evenodd" d="M8.84,11.17C69.36,84.67,162.6,199,167.28,205.18c3.89,5.2,2.33,10.13-4.16,13.77-3.64,2.08-11.17,4.16-14.8,4.16a18.74,18.74,0,0,1-12.47-5.46c-2.34-2.34-12.47-17.14-35.32-52.72-17.41-27.27-32.21-49.87-32.47-50.13-1-.52-1-.52,30.65,56.1,20,35.58,26.49,48.31,26.49,49.87,0,3.37-1,5.19-5.19,9.87-7,7.79-10.13,16.62-12.47,35.06-2.6,20.52-9.61,35.06-29.61,59.74C66.24,340,64.42,342.58,61.57,348.55c-3.64,7.28-4.68,11.43-5.2,20.78-.52,9.87.52,16.11,3.38,25.46,2.6,8.31,5.45,13.76,12.47,24.41,6,9.35,9.61,16.36,9.61,19,0,2.08.52,2.08,9.87,0,22.33-5.19,40.77-14,50.9-24.93,6.24-6.76,7.79-10.39,7.79-19.74,0-6-.26-7.28-1.81-10.91-2.6-5.72-7.54-10.39-18.19-17.66-14-9.61-20-17.41-21.55-27.79-1.3-8.83.26-14.81,8-31.17,8-16.88,10.13-23.9,11.43-41,.78-10.91,2.07-15.32,5.19-18.7,3.38-3.63,6.23-4.93,14.29-6,13.24-1.82,21.81-5.2,28.57-11.69,6-5.45,8.57-10.91,8.83-19l.26-6L182.08,200C169.87,186,.79,0,0,0-.25,0,3.91,4.93,8.84,11.17ZM88.58,380.5a10.71,10.71,0,0,0-3.38-14.28C80.79,363.36,74,364.66,74,368.55a2.65,2.65,0,0,0,2.08,2.6c2.34,1.3,2.6,2.6.78,5.45s-1.82,5.46.52,7.28C81.05,386.73,86,385.18,88.58,380.5Z"/>
                        <path fill-rule="evenodd" d="M193.77,243.88c-6.24,1.82-12.21,8.57-14,15.33-1,4.15-.52,11.69,1.3,14,2.86,3.64,5.46,4.68,12.73,4.68,14.28,0,26.49-6.24,27.79-13.77,1.3-6.23-4.16-14.8-11.69-18.7C206,243.36,197.92,242.59,193.77,243.88Zm16.62,13c2.08-3.12,1.3-6.49-2.6-8.83-7-4.42-17.66-.78-17.66,6,0,3.38,5.46,7,10.65,7C204.16,261,208.83,259,210.39,256.87Z"/>
                    </svg>`;

                    // PancakeSwap bunny icon (CAKE orange-brown) - paths styled with CAKE brown fill
                    const pancakeIconSvg = `<svg class="proto-brand-icon" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
                        <ellipse cx="50" cy="72" rx="26" ry="18" fill="#d1884f" opacity="0.9"/>
                        <ellipse cx="50" cy="68" rx="22" ry="14" fill="#d1884f"/>
                        <ellipse cx="36" cy="32" rx="8" ry="20" fill="#d1884f" opacity="0.9" transform="rotate(-15 36 32)"/>
                        <ellipse cx="64" cy="32" rx="8" ry="20" fill="#d1884f" opacity="0.9" transform="rotate(15 64 32)"/>
                        <ellipse cx="50" cy="58" rx="22" ry="20" fill="#d1884f"/>
                        <ellipse cx="50" cy="60" rx="18" ry="16" fill="#d1884f"/>
                        <circle cx="42" cy="55" r="3.5" fill="none" stroke="#ffffff" stroke-width="2"/>
                        <circle cx="58" cy="55" r="3.5" fill="none" stroke="#ffffff" stroke-width="2"/>
                    </svg>`;
                    const brandIcon = uniProtocol === 'pancakeswap' ? pancakeIconSvg : uniswapIconSvg;
                    const linkTitle = uniProtocol === 'pancakeswap' ? 'View on PancakeSwap' : 'View on Uniswap';
                    uniLinkHtml = `
                        <a href="${uniHref}" target="_blank" class="pool-label-link pool-label-link--${uniProtocol}" data-tooltip="${linkTitle}" onclick="event.stopPropagation();">
                            ${brandIcon}
                        </a>
                    `;
                }

                let revertHtml = '';
                if (pool_addr) {
                    const protocolNameLower = protocolName.toLowerCase();
                    const networkLower = (networkName || 'ethereum').toLowerCase();

                    let showRevert = false;
                    let revertNet = 'mainnet';
                    if (networkLower.includes('base')) revertNet = 'base';
                    else if (networkLower.includes('arbitrum')) revertNet = 'arbitrum';
                    else if (networkLower.includes('optimism')) revertNet = 'optimism';
                    else if (networkLower.includes('polygon')) revertNet = 'polygon';
                    else if (networkLower.includes('bnb') || networkLower.includes('bsc')) revertNet = 'bnb';

                    let revertProto = '';
                    if (protocolNameLower.includes('uniswap v4') || (protocolNameLower.includes('uniswap') && protocolNameLower.includes('v4'))) {
                        revertProto = 'uniswapv4';
                        showRevert = true;
                    } else if (protocolNameLower.includes('uniswap v3') || protocolNameLower === 'uniswap' || (protocolNameLower.includes('uniswap') && protocolNameLower.includes('v3'))) {
                        revertProto = 'uniswapv3';
                        showRevert = true;
                    } else if (protocolNameLower.includes('pancakeswap v3') || (protocolNameLower.includes('pancake') && protocolNameLower.includes('v3'))) {
                        if (revertNet === 'bnb' || revertNet === 'arbitrum') {
                            revertProto = 'pancakeswapv3';
                            showRevert = true;
                        }
                    } else if (protocolNameLower.includes('aerodrome')) {
                        if (revertNet === 'base') {
                            revertProto = 'aerodrome';
                            showRevert = true;
                        }
                    }

                    if (showRevert) {
                        const revertUrl = `https://revert.finance/#/pool/${revertNet}/${revertProto}/${pool_addr.toLowerCase()}`;
                        revertHtml = `
                            <a href="${revertUrl}" target="_blank" class="revert-link" data-tooltip="Analyze on Revert Finance" onclick="event.stopPropagation();">
                                <img src="/static/assets/revert.svg" alt="Revert Finance" class="revert-icon" />
                            </a>
                        `;
                    }
                }

                // DexScreener chart link — off by default
                let dexscreenerHtml = '';
                if (pool_addr) {
                    const networkLower = (networkName || 'ethereum').toLowerCase();
                    let dsNet = 'ethereum';
                    if (networkLower.includes('base')) dsNet = 'base';
                    else if (networkLower.includes('arbitrum')) dsNet = 'arbitrum';
                    else if (networkLower.includes('optimism')) dsNet = 'optimism';
                    else if (networkLower.includes('polygon')) dsNet = 'polygon';
                    else if (networkLower.includes('bnb') || networkLower.includes('bsc')) dsNet = 'bsc';
                    else if (networkLower.includes('avalanche')) dsNet = 'avalanche';

                    const dexscreenerUrl = `https://dexscreener.com/${dsNet}/${pool_addr}`;
                    dexscreenerHtml = `
                        <a href="${dexscreenerUrl}" target="_blank" class="lp-link dexscreener-link" data-tooltip="View on DexScreener" onclick="event.stopPropagation();">
                            <img src="/static/assets/dexscreener.ico" alt="DexScreener" class="lp-link-icon dexscreener-icon" style="border-radius: 50%;" />
                        </a>
                    `;
                }

                // DeFi Llama yields link — off by default
                let defillamaHtml = '';
                if (stats && stats.defillama_uuid) {
                    const defillamaUrl = `https://defillama.com/yields/pool/${stats.defillama_uuid}`;
                    defillamaHtml = `
                        <a href="${defillamaUrl}" target="_blank" class="lp-link defillama-link" data-tooltip="View on DeFi Llama" onclick="event.stopPropagation();">
                            <img src="/static/assets/defillama.ico" alt="DeFi Llama" class="lp-link-icon defillama-icon" style="border-radius: 50%;" />
                        </a>
                    `;
                }

                let labelContent = `
                    <div class="label-pane fee-pane" data-tooltip="Tier">
                        <span class="fee-pill">${feeDisplay}</span>
                    </div>
                `;
                if (aprDisplay) {
                    labelContent += `
                        <div class="label-pane apr-pane" data-tooltip="APR">
                            <span class="apr-label">${aprDisplay}</span>
                        </div>
                    `;
                }

                let linksContent = '';
                if (uniLinkHtml || revertHtml || dexscreenerHtml || defillamaHtml) {
                    linksContent = `
                        <div class="label-pane links-pane">
                            ${uniLinkHtml}
                            ${revertHtml}
                            ${dexscreenerHtml}
                            ${defillamaHtml}
                        </div>
                    `;
                }

                const isClickable = !!uniHref;
                html += `
                    <div class="route-hop ${protocolClass} ${isClickable ? 'clickable-route-segment' : ''}">
                        <div class="route-hop-arrow ${protocolClass}" data-tooltip="${arrowTooltip}">
                            <div class="arrow-line">
                                <div class="route-hop-label">
                                    ${labelContent}
                                    ${linksContent}
                                </div>
                            </div>
                            <svg class="arrow-head" viewBox="0 0 8 14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="1,1 7,7 1,13"/>
                            </svg>
                        </div>
                    </div>
                `;
            } else {
                html += `
                    <a href="${getCmcUrl(token)}" target="_blank" class="token-badge-link" onclick="event.stopPropagation();">
                        <span class="token-badge">${tokenIconHtml(token)} ${token}</span>
                    </a>
                `;
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
