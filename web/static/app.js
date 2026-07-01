document.addEventListener('DOMContentLoaded', async () => {
    const analyzeBtn = document.getElementById('analyze-btn');
    const startTokenInput = document.getElementById('start-token');
    const endTokenInput = document.getElementById('end-token');
    const resultsSection = document.getElementById('results-section');
    const routesBody = document.getElementById('routes-body');
    const loader = document.getElementById('loader');
    const noDataMsg = document.getElementById('no-data');
    const startDateInput = document.getElementById('start-date');
    const endDateInput = document.getElementById('end-date');

    let tokenImageMap = {};

    // Fetch available date range from API and set defaults
    try {
        const response = await fetch('/api/routes/date-range');
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

    // Fetch official token logos from backend
    try {
        const response = await fetch('/api/coin/list');
        const coins = await response.json();
        coins.forEach(coin => {
            if (coin.symbol && coin.image) {
                tokenImageMap[coin.symbol.toUpperCase()] = coin.image;
            }
        });
    } catch (error) {
        console.error('Error fetching token images:', error);
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

    const getRouteAvgApr = (route) => {
        let totalApr = 0;
        let hopCount = 0;
        if (route.path_tokens) {
            route.path_tokens.forEach((item, idx) => {
                if (idx % 2 === 1 && typeof item === 'object') {
                    totalApr += (item.apr || 0);
                    hopCount++;
                }
            });
        }
        return hopCount === 1 ? totalApr : 0;
    };

    const filterAndRenderRoutes = () => {
        if (!currentRoutes) return;

        const minAprInput = document.getElementById('min-apr-filter');
        const minMktInput = document.getElementById('min-mkt-filter');
        const minTxsInput = document.getElementById('min-txs-filter');
        const acyclicCheckbox = document.getElementById('acyclic-filter');
        const networkFilter = document.getElementById('network-filter');
        const protocolFilter = document.getElementById('protocol-filter');

        const minAprVal = minAprInput ? parseFloat(minAprInput.value) || 0 : 0;
        const minMktVal = minMktInput ? parseFloat(minMktInput.value) || 0 : 0;
        const minTxsVal = minTxsInput ? parseInt(minTxsInput.value) || 0 : 0;
        const acyclicOnly = acyclicCheckbox ? acyclicCheckbox.checked : false;
        const selectedNetwork = networkFilter ? networkFilter.value : 'all';
        const selectedProtocol = protocolFilter ? protocolFilter.value : 'all';

        const filtered = currentRoutes.filter(route => {
            // Network filter
            if (selectedNetwork !== 'all' && (route.network || 'Ethereum') !== selectedNetwork) {
                return false;
            }
            // Protocol filter
            if (selectedProtocol !== 'all') {
                const routePath = route.path || '';
                if (!routePath.includes(selectedProtocol)) {
                    return false;
                }
            }
            // Min APR filter
            const avgAprPct = getRouteAvgApr(route) * 100;
            if (avgAprPct < minAprVal) return false;

            // Min Market Size filter
            const marketSize = route.market_size || 0;
            if (marketSize < minMktVal) return false;

            // Min TXs filter
            const txCount = route.count || 0;
            if (txCount < minTxsVal) return false;

            // Acyclic filter
            if (acyclicOnly) {
                let tokens = [];
                if (route.path_tokens) {
                    for (let i = 0; i < route.path_tokens.length; i++) {
                        if (i % 2 === 0) tokens.push(route.path_tokens[i]);
                    }
                } else {
                    const parts = route.path.split(' ');
                    for (let i = 0; i < parts.length; i++) {
                        if (i % 4 === 0) tokens.push(parts[i]);
                    }
                }
                const isAcyclic = new Set(tokens).size === tokens.length;
                if (!isAcyclic) return false;
            }

            return true;
        });

        renderRoutes(filtered);
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
            const selectedNetwork = document.getElementById('network-filter')?.value || 'all';
            let url = `/api/routes/analyze?start_token=${startToken}&end_token=${endToken}`;
            if (startDate) url += `&start_date=${startDate}`;
            if (endDate) url += `&end_date=${endDate}`;
            if (selectedNetwork && selectedNetwork !== 'all') {
                url += `&network=${selectedNetwork}`;
            }

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
            currentRoutes = data.routes;
            filterAndRenderRoutes();

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
            filterAndRenderRoutes();
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
            // Calculate Route APR (only valid for single-hop)
            let totalApr = 0;
            let hopCount = 0;
            if (route.path_tokens) {
                route.path_tokens.forEach((item, idx) => {
                    if (idx % 2 === 1 && typeof item === 'object') {
                        totalApr += (item.apr || 0);
                        hopCount++;
                    }
                });
            }
            const avgApr = hopCount === 1 ? totalApr : 0;
            const aprClass = hopCount > 1 ? 'text-muted' : (avgApr > 0.5 ? 'text-success font-bold' : (avgApr > 0 ? 'text-success' : 'text-muted'));

            // Use backend pre-calculated string if available, otherwise format locally
            const aprDisplay = hopCount > 1 ? '-' : (route.apr_str || (hopCount === 1 ? (avgApr * 100).toFixed(1) + '%' : 'N/A'));

            const networkVal = route.network || 'Ethereum';
            const networkClass = networkVal.toLowerCase();

            const row = document.createElement('tr');
            row.innerHTML = `
                <td class="path-cell">${renderPath(route)}</td>
                <td class="col-network"><span class="badge ${networkClass}">${networkVal}</span></td>
                <td class="col-tx-count">${route.count.toLocaleString()}</td>
                <td class="col-apr ${aprClass}">${aprDisplay}</td>
                <td class="col-volume font-bold">${formatUSD(route.volume)}</td>
                <td class="col-market-size">${formatUSD(route.market_size || 0)}</td>
                <td class="col-avg-volume">${formatUSD(route.avg_volume)}</td>
                <td class="col-pct-volume accent-text">${route.pct_volume.toFixed(1)}%</td>
            `;
            routesBody.appendChild(row);
        });
        updateColumnVisibility();
    };

    const tokenIconUrl = (symbol) => {
        const s = symbol.toLowerCase();
        return `https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25fa5/128/color/${s}.png`;
    };

    const tokenIconHtml = (symbol, size = 16) => {
        const uppercaseSymbol = symbol.toUpperCase();
        
        // 1. Try to get the specific icon directly from the CoinGecko loaded map
        let url = tokenImageMap[uppercaseSymbol];
        
        if (!url) {
            // 2. Map wrapped / pegged assets to native counterparts as a fallback
            const logoMappings = {
                'WETH': 'ETH',
                'WBTC': 'BTC',
                'CBBTC': 'BTC',
                'TBTC': 'BTC',
                'KBTC': 'BTC',
                'LBTC': 'BTC',
                'FBTC': 'BTC'
            };
            
            let mappedSymbol = uppercaseSymbol;
            if (logoMappings[uppercaseSymbol]) {
                mappedSymbol = logoMappings[uppercaseSymbol];
            }
            
            // 3. Try mapped symbol from map, or use default CDN fallback
            url = tokenImageMap[mappedSymbol] || tokenIconUrl(mappedSymbol);
        }
        
        return `<img src="${url}" width="${size}" height="${size}" onerror="this.src='/static/favicon.png'" style="border-radius: 50%; vertical-align: middle; flex-shrink: 0;">`;
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

        const parseProtocol = (feeString) => {
            let cleanFee = feeString || '';
            let protocolName = 'Uniswap';
            let protocolClass = 'v3';
            let networkName = '';

            if (feeString && feeString.includes('|')) {
                const parts = feeString.split('|');
                cleanFee = parts[0];
                if (parts[1]) {
                    protocolName = parts[1];
                    const rawProto = parts[1].toLowerCase();
                    if (rawProto === 'uniswap v3' || rawProto === 'v3') {
                        protocolClass = 'v3';
                    } else if (rawProto === 'uniswap v4' || rawProto === 'v4') {
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

        let html = '<div class="route-path-container">';

        tokens.forEach((token, idx) => {
            html += `<span class="token-badge">${tokenIconHtml(token)} ${token}</span>`;

            if (idx < tokens.length - 1) {
                const item = items[idx];
                let displayVal = '?';
                let tooltip = '';
                let protocolClass = '';

                if (item !== undefined && item !== null) {
                    if (typeof item === 'object') {
                        // Backend enriched object
                        const parsed = parseProtocol(item.fee);
                        let cleanFee = parsed.cleanFee;
                        let protocolName = parsed.protocolName;
                        let networkName = parsed.networkName;
                        protocolClass = parsed.protocolClass;

                        // Normalize Dynamic to dyn, and convert basis points to percentages
                        let dispFee = cleanFee;
                        const parsedFee = parseFloat(cleanFee);
                        if (!isNaN(parsedFee) && parsedFee >= 5) {
                            dispFee = (parsedFee / 10000) + '%';
                            cleanFee = dispFee;
                        }
                        if (cleanFee.toLowerCase() === 'dynamic') {
                            dispFee = 'dyn';
                        }

                        if (isAprMode) {
                            if (item.apr !== undefined && item.apr !== null && item.apr > 0) {
                                // For very small APRs, show more precision
                                const aprVal = item.apr * 100;
                                displayVal = aprVal < 0.1 ? aprVal.toFixed(3) + '%' : aprVal.toFixed(1) + '%';
                            } else {
                                displayVal = dispFee + '*';
                            }
                        } else {
                            displayVal = dispFee;
                        }

                        tooltip = `APR: ${item.apr_str || 'N/A'}\nTier: ${cleanFee}\nProtocol: ${protocolName}\nNetwork: ${networkName || 'Ethereum'}`;
                    } else if (typeof item === 'string') {
                        const parsed = parseProtocol(item);
                        let cleanFee = parsed.cleanFee;
                        let protocolName = parsed.protocolName;
                        let networkName = parsed.networkName;
                        protocolClass = parsed.protocolClass;

                        let dispFee = cleanFee;
                        const parsedFee = parseFloat(cleanFee);
                        if (!isNaN(parsedFee) && parsedFee >= 5) {
                            dispFee = (parsedFee / 10000) + '%';
                            cleanFee = dispFee;
                        }
                        if (cleanFee.toLowerCase() === 'dynamic') {
                            displayVal = 'dyn';
                        } else {
                            displayVal = dispFee;
                        }
                        tooltip = `APR: N/A\nTier: ${cleanFee}\nProtocol: ${protocolName}\nNetwork: ${networkName || 'Ethereum'}`;
                    } else {
                        const feeNum = parseFloat(item);
                        if (!isNaN(feeNum)) {
                            displayVal = (feeNum / 10000) + '%';
                        }
                        tooltip = `APR: N/A\nTier: ${displayVal}\nProtocol: Unknown`;
                    }
                }

                html += `
                        <div class="route-arrow-wrapper ${protocolClass}" data-tooltip="${tooltip}">
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
            } else if (key === 'apr') {
                valA = getRouteAvgApr(a);
                valB = getRouteAvgApr(b);
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

        filterAndRenderRoutes();
    };

    const updateColumnVisibility = () => {
        const checkboxes = document.querySelectorAll('#column-selector-dropdown input[type="checkbox"]');
        checkboxes.forEach(cb => {
            const colClass = `col-${cb.dataset.col}`;
            const isVisible = cb.checked;
            
            // Toggle visibility for headers and cells
            document.querySelectorAll(`.${colClass}`).forEach(el => {
                if (isVisible) {
                    el.classList.remove('hidden-column');
                } else {
                    el.classList.add('hidden-column');
                }
            });
        });
    };

    // Event listeners for sorting
    document.getElementById('sort-count').addEventListener('click', () => sortRoutes('count', 'sort-count'));
    document.getElementById('sort-apr').addEventListener('click', () => sortRoutes('apr', 'sort-apr'));
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

    // Column selector UI controls
    const colBtn = document.getElementById('column-selector-btn');
    const colDropdown = document.getElementById('column-selector-dropdown');

    if (colBtn && colDropdown) {
        colBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            colDropdown.classList.toggle('hidden');
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!colDropdown.contains(e.target) && e.target !== colBtn) {
                colDropdown.classList.add('hidden');
            }
        });

        // Toggle columns on checkbox change
        colDropdown.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', () => {
                updateColumnVisibility();
            });
        });
    }

    // Input listeners for real-time filtering
    ['min-apr-filter', 'min-mkt-filter', 'min-txs-filter'].forEach(id => {
        const input = document.getElementById(id);
        if (input) {
            input.addEventListener('input', () => {
                filterAndRenderRoutes();
            });
        }
    });

    const acyclicCheckbox = document.getElementById('acyclic-filter');
    if (acyclicCheckbox) {
        acyclicCheckbox.addEventListener('change', () => {
            filterAndRenderRoutes();
        });
    }

    const networkFilterSelect = document.getElementById('network-filter');
    if (networkFilterSelect) {
        networkFilterSelect.addEventListener('change', () => {
            filterAndRenderRoutes();
        });
    }

    const protocolFilterSelect = document.getElementById('protocol-filter');
    if (protocolFilterSelect) {
        protocolFilterSelect.addEventListener('change', () => {
            filterAndRenderRoutes();
        });
    }
});
