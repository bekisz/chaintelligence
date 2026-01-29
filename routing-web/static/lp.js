document.addEventListener('DOMContentLoaded', () => {
    const totalPortfolioValueEl = document.getElementById('total-portfolio-value');
    const totalRewardsEl = document.getElementById('total-rewards');
    const positionsGrid = document.getElementById('positions-grid');
    const loader = document.getElementById('loader');
    const noDataMsg = document.getElementById('no-data');

    const formatUSD = (amount) => {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2
        }).format(amount);
    };

    const cleanLabel = (label) => {
        if (!label) return 'Position';
        // "ETH / USDC (Token ID: 103718)" -> "ETH - USDC"
        return label.split(' (Token ID:')[0].replace(/\s*\/\s*/g, ' - ');
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
            // Heuristic: latest snapshots for each unique position_label
            const latestPositions = {};
            data.forEach(pos => {
                const key = `${pos.protocol}-${pos.position_label}-${pos.network}`;
                if (!latestPositions[key] || new Date(pos.timestamp) > new Date(latestPositions[key].timestamp)) {
                    latestPositions[key] = pos;
                }
            });

            const uniqueLatest = Object.values(latestPositions);
            // Sort by value descending
            uniqueLatest.sort((a, b) => b.balance_usd - a.balance_usd);

            const totalValue = uniqueLatest.reduce((sum, p) => sum + p.balance_usd, 0);
            const totalRewards = uniqueLatest.reduce((sum, p) => sum + p.total_unclaimed_usd, 0);

            totalPortfolioValueEl.textContent = formatUSD(totalValue);
            totalRewardsEl.textContent = formatUSD(totalRewards);

            // Render rows
            uniqueLatest.forEach(pos => {
                const row = document.createElement('div');
                row.className = 'position-row glass';

                const timeStr = new Date(pos.timestamp).toLocaleString();
                const cleanedLabel = cleanLabel(pos.position_label);

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


        } catch (error) {
            console.error('Error fetching LP summary:', error);
            noDataMsg.textContent = `Error: ${error.message}`;
            noDataMsg.classList.remove('hidden');
        } finally {
            loader.classList.add('hidden');
        }
    };

    fetchLPSummary();
});
