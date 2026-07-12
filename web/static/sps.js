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
                    aprDisplay = aprVal < 0.1 ? aprVal.toFixed(3) + '%' : aprVal.toFixed(1) + '%';
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
                    const uniswapIconSvg = `<svg class="proto-brand-icon" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
                        <path d="M1.848 12.164a.823.823 0 0 1-.127-1.157L3.35 8.966a.823.823 0 0 1 1.284 1.026l-1.63 2.043a.823.823 0 0 1-1.156.129zm1.693-5.18l1.452.363a.823.823 0 0 1 .585.992.823.823 0 0 1-.992.585l-1.452-.363a.823.823 0 0 1 .407-1.577zm5.698-1.743c.454 0 .823.369.823.823v2.17c0 .454-.369.823-.823.823a.823.823 0 0 1-.823-.823v-2.17c0-.454.369-.823.823-.823zm2.463-3.86a.823.823 0 0 1 1.109-.297l1.884 1.088a.823.823 0 0 1-.823 1.425l-1.884-1.088a.823.823 0 0 1-.286-1.128zm10.15 10.78a9.49 9.49 0 0 1-5.704 8.708v-2.73a.823.823 0 0 0-.823-.823h-2.057a3.086 3.086 0 0 1-3.086-3.086v-.727a9.49 9.49 0 0 1 11.67-1.342z"/>
                    </svg>`;
                    const pancakeIconSvg = `<svg class="proto-brand-icon" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
                        <ellipse cx="50" cy="72" rx="26" ry="18" fill="currentColor" opacity="0.9"/>
                        <ellipse cx="50" cy="68" rx="22" ry="14" fill="currentColor"/>
                        <ellipse cx="36" cy="32" rx="8" ry="20" fill="currentColor" opacity="0.9" transform="rotate(-15 36 32)"/>
                        <ellipse cx="64" cy="32" rx="8" ry="20" fill="currentColor" opacity="0.9" transform="rotate(15 64 32)"/>
                        <ellipse cx="50" cy="58" rx="22" ry="20" fill="currentColor"/>
                        <ellipse cx="50" cy="60" rx="18" ry="16" fill="currentColor"/>
                        <circle cx="42" cy="55" r="3.5" fill="none" stroke="currentColor" stroke-width="2"/>
                        <circle cx="58" cy="55" r="3.5" fill="none" stroke="currentColor" stroke-width="2"/>
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

                    let revertNet = 'mainnet';
                    if (networkLower.includes('base')) revertNet = 'base';
                    else if (networkLower.includes('arbitrum')) revertNet = 'arbitrum';
                    else if (networkLower.includes('optimism')) revertNet = 'optimism';
                    else if (networkLower.includes('polygon')) revertNet = 'polygon';

                    let revertProto = 'uniswapv3';
                    if (protocolNameLower.includes('uniswap v4') || protocolNameLower.includes('uniswap-v4') || protocolNameLower.includes('v4')) {
                        revertProto = 'uniswapv4';
                    } else if (protocolNameLower.includes('uniswap v2') || protocolNameLower.includes('uniswap-v2') || protocolNameLower.includes('v2')) {
                        revertProto = 'uniswapv2';
                    } else if (protocolNameLower.includes('pancake')) {
                        revertProto = 'pancakeswapv3';
                    }

                    const revertUrl = `https://revert.finance/#/pool/${revertNet}/${revertProto}/${pool_addr}`;
                    revertHtml = `
                        <a href="${revertUrl}" target="_blank" class="revert-link" data-tooltip="Analyze on Revert Finance" onclick="event.stopPropagation();">
                            <svg class="revert-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>
                                <path d="M21 3v5h-5"/>
                                <path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>
                                <path d="M3 21v-5h5"/>
                            </svg>
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
                if (uniLinkHtml || revertHtml) {
                    linksContent = `
                        <div class="label-pane links-pane">
                            ${uniLinkHtml}
                            ${revertHtml}
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
