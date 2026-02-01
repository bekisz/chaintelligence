// Global state for filtering and sorting
let allPositions = [];
let currentFilters = {
    network: 'all',
    protocol: 'all',
    search: ''
};
let currentSort = 'value-desc';

document.addEventListener('DOMContentLoaded', () => {
    const totalPortfolioValueEl = document.getElementById('total-portfolio-value');
    const totalRewardsEl = document.getElementById('total-rewards');
    const positionsGrid = document.getElementById('positions-grid');
    const loader = document.getElementById('loader');
    const noDataMsg = document.getElementById('no-data');

    // Filter and sort controls
    const networkFilter = document.getElementById('network-filter');
    const protocolFilter = document.getElementById('protocol-filter');
    const searchFilter = document.getElementById('search-filter');
    const sortBy = document.getElementById('sort-by');

    const formatUSD = (amount) => {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2
        }).format(amount);
    };

    const cleanLabel = (label) => {
        if (!label) return 'Position';
        // "ETH / USDC (Token ID: 103718)" -> "ETH - USDC #103718"
        return label.replace(/\(Token ID:\s*([^\)]+)\)/i, '#$1').replace(/\s*\/\s*/g, ' - ');
    };

    const applyFiltersAndSort = () => {
        let filtered = [...allPositions];

        // Apply network filter
        if (currentFilters.network !== 'all') {
            filtered = filtered.filter(p => p.network === currentFilters.network);
        }

        // Apply protocol filter
        if (currentFilters.protocol !== 'all') {
            filtered = filtered.filter(p => p.protocol === currentFilters.protocol);
        }

        // Apply search filter
        if (currentFilters.search) {
            const search = currentFilters.search.toLowerCase();
            filtered = filtered.filter(p => {
                const label = cleanLabel(p.position_label).toLowerCase();
                return label.includes(search);
            });
        }

        // Apply "Hide Closed" filter
        const hideClosed = document.getElementById('hide-closed-filter')?.checked;
        if (hideClosed) {
            filtered = filtered.filter(p => !p.isClosed);
        }

        // Apply sorting
        switch (currentSort) {
            case 'value-desc':
                filtered.sort((a, b) => b.balance_usd - a.balance_usd);
                break;
            case 'value-asc':
                filtered.sort((a, b) => a.balance_usd - b.balance_usd);
                break;
            case 'rewards-desc':
                filtered.sort((a, b) => b.total_unclaimed_usd - a.total_unclaimed_usd);
                break;
            case 'rewards-asc':
                filtered.sort((a, b) => a.total_unclaimed_usd - b.total_unclaimed_usd);
                break;
            case 'pair':
                filtered.sort((a, b) => {
                    const labelA = cleanLabel(a.position_label);
                    const labelB = cleanLabel(b.position_label);
                    return labelA.localeCompare(labelB);
                });
                break;
        }

        renderPositions(filtered);
    };

    const renderPositions = (positions) => {
        if (!positions || positions.length === 0) {
            positionsGrid.innerHTML = '<div class="no-data-msg">No positions match your filters.</div>';
            return;
        }

        positionsGrid.innerHTML = '';

        positions.forEach(pos => {
            const row = document.createElement('div');
            row.className = `position-row glass ${pos.isClosed ? 'closed-pos' : ''}`;
            if (pos.isClosed) row.style.opacity = '0.6';

            const timeStr = new Date(pos.timestamp).toLocaleString();
            let cleanedLabel = cleanLabel(pos.position_label);
            if (pos.range_data && pos.range_data.token_id && !cleanedLabel.includes('#')) {
                cleanedLabel += ` #${pos.range_data.token_id}`;
            }

            if (pos.isClosed) {
                cleanedLabel += ' <span style="color:#f87171; font-size:0.8em;">(Closed)</span>';
            }

            // Get images (limit to 2 for LP pairs)
            const images = (pos.images && pos.images.length > 0) ? pos.images.slice(0, 2) : ['/static/favicon.png'];
            const iconsHtml = images.map(img => `<img src="${img}" class="pos-icon-stacked" onerror="this.src='/static/favicon.png'">`).join('');

            // Calculate accrual badge
            const delta = pos.reward_delta_usd || 0;
            const accrualHtml = delta > 0
                ? `<span class="accrual-tag positive">+${formatUSD(delta)} accrued</span>`
                : delta < 0
                    ? `<span class="accrual-tag negative">${formatUSD(delta)} (claimed?)</span>`
                    : '';

            // Calculate range data - will use real data if available from API
            const rangeData = calculateRangeData(pos);
            const rangeHtml = createRangeIndicator(rangeData);

            row.innerHTML = `
                <div class="pos-info">
                    <div class="pos-main">
                        <div class="pos-header-with-icon">
                            <div class="pos-icons-stack">
                                ${iconsHtml}
                            </div>
                            <h4>${cleanedLabel}</h4>
                        </div>
                        <div class="pos-meta">
                            <span class="badge ${pos.network.toLowerCase()}">${pos.network}</span>
                            <span class="protocol-tag">${pos.protocol}</span>
                        </div>
                    </div>
                    ${rangeHtml}
                </div>
                
                <div class="pos-assets">
                    ${pos.assets.filter(a => a.symbol).map(asset => `
                        <div class="asset-item">
                            <span class="asset-sym">${asset.symbol}</span>
                            <span class="asset-amt">${Number(asset.balance || 0).toFixed(4)}</span>
                            <span class="asset-usd">${formatUSD(Number(asset.balanceUSD || 0))}</span>
                        </div>
                    `).join('')}
                </div>
                
                <div class="pos-rewards">
                    <div class="reward-items">
                        ${pos.unclaimed.filter(u => u.symbol).map(u => `
                            <div class="reward-item">
                                <span class="reward-label">${u.symbol}</span>
                                <span class="reward-val">${Number(u.balance || 0).toFixed(4)}</span>
                            </div>
                        `).join('')}
                    </div>
                    <div class="reward-footer">
                        ${accrualHtml}
                        <span class="reward-total">${formatUSD(Number(pos.total_unclaimed_usd || 0))}</span>
                    </div>
                </div>
                
                <div class="pos-value">
                    <span class="value-label">Total Position</span>
                    <span class="value-amt">${formatUSD(Number(pos.balance_usd || 0))}</span>
                    <span class="timestamp">${timeStr}</span>
                </div>
            `;
            positionsGrid.appendChild(row);
        });
    };

    const calculateRangeData = (position) => {
        if (position.range_data && position.range_data.token_id) {
            const rd = position.range_data;
            // Ensure numeric types
            return {
                inRange: rd.in_range,
                minPrice: Number(rd.price_lower),
                maxPrice: Number(rd.price_upper),
                currentPrice: Number(rd.current_price)
            };
        }
        return null;
    };

    const createRangeIndicator = (rangeData) => {
        if (!rangeData) return '';
        const { inRange, minPrice, maxPrice, currentPrice } = rangeData;

        // Validations
        if (minPrice == null || maxPrice == null || currentPrice == null) return '';

        const fmt = (n) => {
            if (n === undefined || n === null || isNaN(n)) return '-';
            return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
        };

        // 1. Determine Visual Bounds (Prices)
        // We want the slider to cover [minPrice, maxPrice] AND currentPrice, plus some padding.

        let visualMin = Math.min(minPrice, currentPrice);
        let visualMax = Math.max(maxPrice, currentPrice);

        // Add padding (15%)
        const rangeSpan = visualMax - visualMin;
        const padding = rangeSpan * 0.15;

        // Ensure visual range isn't zero
        if (rangeSpan === 0) {
            visualMin = visualMin * 0.9;
            visualMax = visualMax * 1.1;
        } else {
            visualMin = Math.max(0, visualMin - padding); // Don't go below 0
            visualMax = visualMax + padding;
        }

        const totalSpan = visualMax - visualMin;
        if (totalSpan === 0) return ''; // Should not happen with padding

        // 3. Determine Status Color
        let statusColorClass = 'status-green'; // Default safe

        if (currentPrice < minPrice || currentPrice > maxPrice) {
            statusColorClass = 'status-red';
        } else {
            // Check proximity to edges within range (e.g., within 10% of edges)
            const rangeWidth = maxPrice - minPrice;
            const distToMin = (currentPrice - minPrice) / rangeWidth;
            const distToMax = (maxPrice - currentPrice) / rangeWidth;

            if (distToMin < 0.15 || distToMax < 0.15) {
                statusColorClass = 'status-yellow';
            }
        }

        // 2. Calculate Percentages for elements
        const getPct = (price) => ((price - visualMin) / totalSpan) * 100;

        const pctMin = Math.max(0, Math.min(100, getPct(minPrice)));
        const pctMax = Math.max(0, Math.min(100, getPct(maxPrice)));
        const pctCurr = Math.max(0, Math.min(100, getPct(currentPrice)));

        // Range Bar width
        const barLeft = Math.min(pctMin, pctMax);
        const barWidth = Math.abs(pctMax - pctMin);

        // Range segment color
        // If status is yellow (warning), use warning color for segment
        let rangeClass = inRange ? 'in-range' : 'out-range';
        if (statusColorClass === 'status-yellow') {
            rangeClass = 'warning';
        }

        return `
            <div class="range-indicator">
                <div class="range-bar-container wide-slider">
                    <!-- The full track is implied by the container width -->
                    
                    <!-- The Active Range Segment -->
                    <div class="range-segment ${rangeClass}" 
                         style="left: ${barLeft}%; width: ${barWidth}%;"></div>
                    
                    <!-- Min/Max Dots -->
                    <div class="range-dot min-dot" style="left: ${pctMin}%;"></div>
                    <div class="range-dot max-dot" style="left: ${pctMax}%;"></div>

                    <!-- Current Price Dot -->
                    <div class="range-current-price" style="left: ${pctCurr}%">
                        <span class="range-label-current ${statusColorClass}">${fmt(currentPrice)}</span>
                        <div class="current-dot ${statusColorClass}"></div>
                    </div>
                </div>
                <div class="range-labels">
                    <!-- Position labels based on calculated percents to align with markers -->
                    <span style="left: ${pctMin}%">Min: ${fmt(minPrice)}</span>
                    <span style="left: ${pctMax}%">Max: ${fmt(maxPrice)}</span>
                </div>
            </div>
        `;
    };

    const fetchLPSummary = async () => {
        loader.classList.remove('hidden');
        positionsGrid.innerHTML = '';
        noDataMsg.classList.add('hidden');

        try {
            const response = await fetch('/api/lp-summary');
            if (!response.ok) throw new Error('Failed to fetch LP summary');

            const data = await response.json();

            if (!data || data.length === 0) {
                noDataMsg.classList.remove('hidden');
                return;
            }

            // Calculate totals (from most recent snapshot of each position_label)
            const latestPositions = {};
            data.forEach(pos => {
                const key = `${pos.protocol}-${pos.position_label}-${pos.network}`;
                if (!latestPositions[key] || new Date(pos.timestamp) > new Date(latestPositions[key].timestamp)) {
                    latestPositions[key] = pos;
                }
            });

            // Detect closed positions (older than 2 hours from latest fetch)
            const uniqueRaw = Object.values(latestPositions);
            if (uniqueRaw.length > 0) {
                const maxTs = Math.max(...uniqueRaw.map(p => new Date(p.timestamp).getTime()));
                allPositions = uniqueRaw.map(p => {
                    const pTs = new Date(p.timestamp).getTime();
                    // Consider closed if older than 2 hours relative to the freshest data
                    p.isClosed = (maxTs - pTs) > (2 * 60 * 60 * 1000);
                    return p;
                });
            } else {
                allPositions = [];
            }

            const totalValue = allPositions.reduce((sum, p) => sum + p.balance_usd, 0);
            const totalRewards = allPositions.reduce((sum, p) => sum + p.total_unclaimed_usd, 0);

            totalPortfolioValueEl.textContent = formatUSD(totalValue);
            totalRewardsEl.textContent = formatUSD(totalRewards);

            // Populate protocol filter options
            const protocols = [...new Set(allPositions.map(p => p.protocol))].sort();
            protocolFilter.innerHTML = '<option value="all">All Protocols</option>' +
                protocols.map(p => `<option value="${p}">${p}</option>`).join('');

            // Apply initial filters and sorting
            applyFiltersAndSort();

        } catch (error) {
            console.error('Error fetching LP summary:', error);
            noDataMsg.textContent = `Error: ${error.message}`;
            noDataMsg.classList.remove('hidden');
        } finally {
            loader.classList.add('hidden');
        }
    };

    // Event listeners for filters and sorting
    if (networkFilter) {
        networkFilter.addEventListener('change', (e) => {
            currentFilters.network = e.target.value;
            applyFiltersAndSort();
        });
    }

    if (protocolFilter) {
        protocolFilter.addEventListener('change', (e) => {
            currentFilters.protocol = e.target.value;
            applyFiltersAndSort();
        });
    }

    if (searchFilter) {
        searchFilter.addEventListener('input', (e) => {
            currentFilters.search = e.target.value;
            applyFiltersAndSort();
        });
    }

    if (sortBy) {
        sortBy.addEventListener('change', (e) => {
            currentSort = e.target.value;
            applyFiltersAndSort();
        });
    }

    const hideClosedCheckbox = document.getElementById('hide-closed-filter');
    if (hideClosedCheckbox) {
        hideClosedCheckbox.addEventListener('change', () => {
            applyFiltersAndSort();
        });
    }

    fetchLPSummary();
});
/* Force reload */
