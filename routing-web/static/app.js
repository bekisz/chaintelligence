document.addEventListener('DOMContentLoaded', async () => {
    const analyzeBtn = document.getElementById('analyze-btn');
    const startTokenInput = document.getElementById('start-token');
    const endTokenInput = document.getElementById('end-token');
    const resultsSection = document.getElementById('results-section');
    const totalVolumeEl = document.getElementById('total-volume');
    const totalTxEl = document.getElementById('total-tx');
    const routesBody = document.getElementById('routes-body');
    const loader = document.getElementById('loader');
    const noDataMsg = document.getElementById('no-data');
    const startDateInput = document.getElementById('start-date');
    const endDateInput = document.getElementById('end-date');

    // Fetch available date range from API and set defaults
    try {
        const response = await fetch('/api/date-range');
        const dateRange = await response.json();

        if (dateRange.min_date && dateRange.max_date) {
            // Set min/max constraints on date inputs
            startDateInput.min = dateRange.min_date;
            startDateInput.max = dateRange.max_date;
            endDateInput.min = dateRange.min_date;
            endDateInput.max = dateRange.max_date;

            // Set default end date to today (or max_date if today is beyond available data)
            const today = new Date().toISOString().split('T')[0];
            const maxDate = dateRange.max_date;
            endDateInput.value = today <= maxDate ? today : maxDate;

            // Set default start date to 30 days before end date (or min_date if less than 30 days available)
            const endDate = new Date(endDateInput.value);
            const thirtyDaysAgo = new Date(endDate);
            thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
            const thirtyDaysAgoStr = thirtyDaysAgo.toISOString().split('T')[0];

            startDateInput.value = thirtyDaysAgoStr >= dateRange.min_date ? thirtyDaysAgoStr : dateRange.min_date;
        }
    } catch (error) {
        console.error('Error fetching date range:', error);
        // Fallback: just set end date to today
        const today = new Date().toISOString().split('T')[0];
        endDateInput.value = today;
    }

    const formatUSD = (amount) => {
        const fractionDigits = amount >= 10 ? 0 : 2;
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: fractionDigits,
            maximumFractionDigits: fractionDigits
        }).format(amount);
    };

    const performAnalysis = async () => {
        const startToken = startTokenInput.value.trim().toUpperCase();
        const endToken = endTokenInput.value.trim().toUpperCase();
        const startDate = startDateInput.value;
        const endDate = endDateInput.value;

        if (!startToken || !endToken) {
            alert('Please enter both start and end tokens.');
            return;
        }

        if (startToken === '*' && endToken === '*') {
            alert('You cannot use * for both start and end tokens. One must be a specific token symbol.');
            return;
        }

        // Show loader, hide results
        loader.classList.remove('hidden');
        resultsSection.classList.add('hidden');
        noDataMsg.classList.add('hidden');

        try {
            let url = `/api/analyze?start_token=${startToken}&end_token=${endToken}`;
            if (startDate) url += `&start_date=${startDate}`;
            if (endDate) url += `&end_date=${endDate}`;

            const response = await fetch(url);
            if (!response.ok) {
                throw new Error('API request failed');
            }

            const data = await response.json();

            if (!data.routes || data.routes.length === 0) {
                let msg = 'No swap data found for the specified period and tokens.';
                if (data.db_range) {
                    msg += `<br/><small>Data available in DB from ${data.db_range.min} to ${data.db_range.max}</small>`;
                }
                noDataMsg.innerHTML = `<p>${msg}</p>`;
                noDataMsg.classList.remove('hidden');
                loader.classList.add('hidden');
                return;
            }

            // Update stats
            totalVolumeEl.textContent = formatUSD(data.total_volume);
            totalTxEl.textContent = data.total_tx.toLocaleString();

            currentRoutes = data.routes;
            renderRoutes(currentRoutes);

            // Show results
            resultsSection.classList.remove('hidden');
        } catch (error) {
            console.error('Error during analysis:', error);
            alert('Analysis failed. Please check the console for details.');
        } finally {
            loader.classList.add('hidden');
        }
    };

    let currentRoutes = [];
    let sortDirection = {
        count: 'desc',
        volume: 'desc',
        mkt: 'desc',
        avg: 'desc',
        pct: 'desc'
    };

    // Event listener for display toggle
    const toggleSwitch = document.getElementById('display-mode-toggle');
    const toggleWrapper = document.getElementById('display-wrapper');

    toggleSwitch.addEventListener('change', (e) => {
        const isApr = e.target.checked;
        if (toggleWrapper) {
            toggleWrapper.classList.toggle('mode-fee', !isApr);
            toggleWrapper.classList.toggle('mode-apr', isApr);
        }
        if (currentRoutes && currentRoutes.length > 0) {
            renderRoutes(currentRoutes);
        }
    });

    // Helper to allow clicking labels
    window.setMode = (mode) => {
        if (mode === 'fee') {
            toggleSwitch.checked = false;
        } else {
            toggleSwitch.checked = true;
        }
        // Trigger change event manually
        toggleSwitch.dispatchEvent(new Event('change'));
    };

    const renderRoutes = (routes) => {
        routesBody.innerHTML = '';
        routes.forEach(route => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td class="path-cell">${renderPath(route)}</td>
                <td>${route.count.toLocaleString()}</td>
                <td class="font-bold">${formatUSD(route.volume)}</td>
                <td>${formatUSD(route.market_size || 0)}</td>
                <td>${formatUSD(route.avg_volume)}</td>
                <td class="accent-text">${route.pct_volume.toFixed(1)}%</td>
            `;
            routesBody.appendChild(row);
        });
    };

    const renderPath = (route) => {
        let tokens = [];
        let items = []; // Can be fee string or object {fee, apr, apr_str}

        if (route.path_tokens) {
            // New format from backend
            for (let i = 0; i < route.path_tokens.length; i++) {
                if (i % 2 === 0) tokens.push(route.path_tokens[i]);
                else items.push(route.path_tokens[i]);
            }
        } else {
            // Fallback: parse old string format "TokenA -- 500 --> TokenB"
            const parts = route.path.split(' ');
            for (let i = 0; i < parts.length; i++) {
                if (i % 4 === 0) tokens.push(parts[i]);
                else if (i % 4 === 2) items.push(parseInt(parts[i]));
            }
        }

        const isAprMode = document.getElementById('display-mode-toggle').checked;

        let html = '<div class="route-path-container">';

        tokens.forEach((token, idx) => {
            html += `<span class="token-badge">${token}</span>`;

            if (idx < tokens.length - 1) {
                const item = items[idx];
                let displayVal = '?';
                let tooltip = '';

                if (item !== undefined && item !== null) {
                    if (typeof item === 'object') {
                        // Backend enriched object
                        if (isAprMode) {
                            displayVal = item.apr_str || 'N/A';
                            // Add fee as tooltip
                            tooltip = `Fee: ${item.fee}`;
                        } else {
                            displayVal = item.fee;
                            tooltip = `APR: ${item.apr_str}`;
                        }
                    } else if (typeof item === 'string' && item.endsWith('%')) {
                        displayVal = item;
                    } else {
                        const feeNum = parseFloat(item);
                        if (!isNaN(feeNum)) {
                            displayVal = (feeNum / 10000) + '%';
                        }
                    }
                }

                html += `
                        <div class="route-arrow-wrapper" title="${tooltip}">
                            <span class="fee-pill ${isAprMode ? 'apr-pill' : ''}">${displayVal}</span>
                            <svg class="route-arrow-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                                <path d="M5 12h14M12 5l7 7-7 7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                        </div>
                    `;
            }
        });

        html += '</div>';
        return html;
    };

    const sortRoutes = (key, headerId) => {
        if (!currentRoutes || currentRoutes.length === 0) return;

        // Toggle direction
        sortDirection[key] = sortDirection[key] === 'desc' ? 'asc' : 'desc';
        const dir = sortDirection[key];

        // Update indicators
        document.querySelectorAll('th.sortable').forEach(th => {
            th.classList.remove('sorted-active');
            const span = th.querySelector('span');
            if (span) span.textContent = '↕';
        });

        const activeHeader = document.querySelector(`#${headerId}`);
        activeHeader.classList.add('sorted-active');
        activeHeader.querySelector('span').textContent = dir === 'asc' ? '↑' : '↓';

        console.log(`Sorting by ${key} (${dir})`);

        // Sort data
        currentRoutes.sort((a, b) => {
            let valA, valB;

            if (key === 'count') {
                valA = a.count;
                valB = b.count;
            } else if (key === 'volume') {
                valA = a.volume;
                valB = b.volume;
            } else if (key === 'mkt') {
                valA = a.market_size || 0;
                valB = b.market_size || 0;
            } else if (key === 'avg') {
                valA = a.avg_volume;
                valB = b.avg_volume;
            } else if (key === 'pct') {
                valA = a.pct_volume;
                valB = b.pct_volume;
            }

            return dir === 'asc' ? valA - valB : valB - valA;
        });

        renderRoutes(currentRoutes);
    };

    // Event listeners for sorting
    document.getElementById('sort-count').addEventListener('click', () => sortRoutes('count', 'sort-count'));
    document.getElementById('sort-vol').addEventListener('click', () => sortRoutes('volume', 'sort-vol'));
    document.getElementById('sort-mkt').addEventListener('click', () => sortRoutes('mkt', 'sort-mkt'));
    document.getElementById('sort-avg').addEventListener('click', () => sortRoutes('avg', 'sort-avg'));
    document.getElementById('sort-pct').addEventListener('click', () => sortRoutes('pct', 'sort-pct'));

    document.getElementById('swap-tokens-btn').addEventListener('click', () => {
        const temp = startTokenInput.value;
        startTokenInput.value = endTokenInput.value;
        endTokenInput.value = temp;
    });

    analyzeBtn.addEventListener('click', performAnalysis);

    // Allow Enter key to trigger analysis
    [startTokenInput, endTokenInput, startDateInput, endDateInput].forEach(input => {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                performAnalysis();
            }
        });
    });
});
