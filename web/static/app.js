let tokenSlugMap = {};

const getCmcUrl = (tokenSymbol) => {
    const symbol = (tokenSymbol || '').toUpperCase().trim();
    const slug = tokenSlugMap[symbol] || symbol.toLowerCase();
    return `https://coinmarketcap.com/currencies/${slug}/`;
};

const formatAprPercent = (pct) => {
    if (pct === null || pct === undefined || isNaN(pct)) return 'N/A';
    return Number(pct.toFixed(1)) + '%';
};

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
    const fetchDateRange = async (network) => {
        try {
            let url = '/api/routes/date-range';
            if (network && network !== 'all') {
                url += `?network=${encodeURIComponent(network)}`;
            }
            const response = await fetch(url);
            const dateRange = await response.json();

            if (dateRange.min_date && dateRange.max_date) {
                // Set min/max constraints on date inputs
                startDateInput.min = dateRange.min_date;
                startDateInput.max = dateRange.max_date;
                endDateInput.min = dateRange.min_date;
                endDateInput.max = dateRange.max_date;

                // Set default end date to the last fetched data time (maxDate)
                const maxDate = dateRange.max_date;
                endDateInput.value = maxDate;

                // Set default start date to 3 days before end date
                const endDate = new Date(endDateInput.value);
                const threeDaysAgo = new Date(endDate);
                threeDaysAgo.setDate(threeDaysAgo.getDate() - 3);
                const threeDaysAgoStr = threeDaysAgo.toISOString().split('T')[0];

                startDateInput.value = threeDaysAgoStr >= dateRange.min_date ? threeDaysAgoStr : dateRange.min_date;
            }
        } catch (error) {
            console.error('Error fetching date range:', error);
            // Fallback: just set end date to today and start date to 3 days ago
            const today = new Date();
            endDateInput.value = today.toISOString().split('T')[0];
            const threeDaysAgo = new Date(today);
            threeDaysAgo.setDate(threeDaysAgo.getDate() - 3);
            startDateInput.value = threeDaysAgo.toISOString().split('T')[0];
        }
    };

    // Initial fetch with the current network filter value
    const queryNetworkSelect = document.getElementById('query-network-filter');

    // Set immediate defaults so the date inputs show real dates right away
    // (the API call below will refine them once it completes)
    const today = new Date();
    const threeDaysAgo = new Date(today);
    threeDaysAgo.setDate(threeDaysAgo.getDate() - 3);
    endDateInput.value = today.toISOString().split('T')[0];
    startDateInput.value = threeDaysAgo.toISOString().split('T')[0];

    fetchDateRange(queryNetworkSelect ? queryNetworkSelect.value : 'all');

    // Re-fetch when the network filter changes
    if (queryNetworkSelect) {
        queryNetworkSelect.addEventListener('change', () => {
            fetchDateRange(queryNetworkSelect.value);
        });
    }

    // Fetch official token logos from backend (non-blocking — populate map when ready)
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
            console.error('Error fetching token images:', error);
        });

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

    const getRouteTvl = (route) => {
        let totalTvl = 0;
        let hopCount = 0;
        if (route.path_tokens) {
            route.path_tokens.forEach((item, idx) => {
                if (idx % 2 === 1 && typeof item === 'object') {
                    totalTvl += (item.tvl || 0);
                    hopCount++;
                }
            });
        }
        // TVL is only meaningful for single-pool routes; multi-hop sorts as 0
        return hopCount === 1 ? totalTvl : 0;
    };

    const filterAndRenderRoutes = () => {
        if (!currentRoutes) return;

        const minAprInput = document.getElementById('min-apr-filter');
        const minMktInput = document.getElementById('min-mkt-filter');
        const minTxsInput = document.getElementById('min-txs-filter');
        const acyclicCheckbox = document.getElementById('acyclic-filter');
        const directOnlyCheckbox = document.getElementById('direct-only-filter');
        const networkFilter = document.getElementById('network-filter');
        const protocolFilter = document.getElementById('protocol-filter');

        const minAprVal = minAprInput ? parseFloat(minAprInput.value) || 0 : 0;
        const minMktVal = minMktInput ? parseFloat(minMktInput.value) || 0 : 0;
        const minTxsVal = minTxsInput ? parseInt(minTxsInput.value) || 0 : 0;
        const acyclicOnly = acyclicCheckbox ? acyclicCheckbox.checked : false;
        const directOnly = directOnlyCheckbox ? directOnlyCheckbox.checked : false;
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

            // Direct-only filter: keep only single-hop routes (one LP between start and end token)
            if (directOnly) {
                let tokenCount = 0;
                if (route.path_tokens) {
                    for (let i = 0; i < route.path_tokens.length; i++) {
                        if (i % 2 === 0) tokenCount++;
                    }
                } else {
                    const parts = route.path.split(' ');
                    for (let i = 0; i < parts.length; i++) {
                        if (i % 4 === 0) tokenCount++;
                    }
                }
                if (tokenCount !== 2) return false;
            }

            return true;
        });

        renderRoutes(filtered);
    };

    const showError = (msg) => {
        noDataMsg.innerHTML = `<div class="empty-state-icon" style="color: var(--red);">⚠</div><p class="empty-state-title">Error</p><p class="empty-state-desc">${msg}</p>`;
        noDataMsg.classList.remove('hidden');
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

        // Show loader, hide results; re-enable post-hoc network filter
        analyzeBtn.disabled = true;
        loader.classList.remove('hidden');
        resultsSection.classList.add('hidden');
        noDataMsg.classList.add('hidden');
        const posthoc = document.getElementById('network-filter');
        if (posthoc) posthoc.disabled = false;

        try {
            const selectedNetwork = document.getElementById('query-network-filter')?.value || 'all';
            let url = `/api/routes/analyze?start_token=${startToken}&end_token=${endToken}`;
            if (startDate) url += `&start_date=${startDate}`;
            if (endDate) url += `&end_date=${endDate}`;
            if (selectedNetwork && selectedNetwork !== 'all') {
                url += `&network=${selectedNetwork}`;
            }

            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`API request failed with status ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            let data = null;

            while (true) {
                const {done, value} = await reader.read();
                if (done) break;
                
                buffer += decoder.decode(value, {stream: true});
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep last incomplete line in buffer
                
                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const msg = JSON.parse(line);
                        if (msg.type === 'progress') {
                            const barFill = document.getElementById('progress-bar-fill');
                            const barText = document.getElementById('progress-text');
                            if (barFill) barFill.style.width = `${msg.pct}%`;
                            if (barText) barText.textContent = msg.message;
                        } else if (msg.type === 'result') {
                            data = msg.data;
                        }
                    } catch (e) {
                        console.error('Error parsing JSON stream line', e);
                    }
                }
            }
            
            if (buffer.trim()) {
                try {
                    const msg = JSON.parse(buffer);
                    if (msg.type === 'result') data = msg.data;
                } catch (e) {}
            }
            
            if (!data) throw new Error('No final result received from stream');

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

            // Restrict the post-hoc network filter to the networks actually
            // queried: if a single network was queried, lock the filter to it
            // (disable the other options); if "All Chains" was queried, leave
            // every network selectable.
            const posthocNetwork = document.getElementById('network-filter');
            if (posthocNetwork) {
                const isAllQuery = !selectedNetwork || selectedNetwork === 'all';
                [...posthocNetwork.options].forEach(opt => {
                    opt.disabled = !isAllQuery && opt.value !== 'all' && opt.value !== selectedNetwork;
                });
                posthocNetwork.disabled = false;
                posthocNetwork.value = isAllQuery ? 'all' : selectedNetwork;
            }

            // Show results
            resultsSection.classList.remove('hidden');
        } catch (error) {
            console.error('Error during analysis:', error);
            showError(error.message || 'Unknown error');
        } finally {
            analyzeBtn.disabled = false;
            loader.classList.add('hidden');
        }
    };

    let currentRoutes = [];
    let sortDirection = {
        count: 'desc',
        volume: 'desc',
        mkt: 'desc',
        avg: 'desc',
        pct: 'desc',
        tvl: 'desc'
    };

    // Event listener for display toggle
    const toggleSwitch = document.getElementById('display-mode-toggle');
    const toggleWrapper = document.getElementById('display-wrapper');

    if (toggleSwitch) {
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
    }

    // Helper to allow clicking labels
    window.setMode = (mode) => {
        if (!toggleSwitch) return;
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
        routes.forEach((route, idx) => {
            // Calculate Route APR (only valid for single-hop) and TVL (only meaningful for single-pool routes)
            let totalApr = 0;
            let hopCount = 0;
            let routeTvl = 0;
            if (route.path_tokens) {
                route.path_tokens.forEach((item, idx) => {
                    if (idx % 2 === 1 && typeof item === 'object') {
                        totalApr += (item.apr || 0);
                        routeTvl += (item.tvl || 0);
                        hopCount++;
                    }
                });
            }
            const avgApr = hopCount === 1 ? totalApr : 0;
            const aprClass = hopCount > 1 ? 'text-muted' : (avgApr > 0.5 ? 'text-success font-bold' : (avgApr > 0 ? 'text-success' : 'text-muted'));

            // Use backend pre-calculated string if available, otherwise format locally
            const aprDisplay = hopCount > 1 ? '-' : (route.apr_str || (hopCount === 1 ? formatAprPercent(avgApr * 100) : 'N/A'));

            // TVL is only shown for single-pool (direct) routes; multi-hop shows '-'
            const tvlDisplay = (hopCount === 1 && routeTvl > 0) ? formatUSD(routeTvl) : '-';

            const networkVal = route.network || 'Ethereum';
            const networkClass = networkVal.toLowerCase();

            const row = document.createElement('tr');
            // Staggered fade-in animation
            row.classList.add('fade-in');
            row.style.animationDelay = `${idx * 30}ms`;
            row.innerHTML = `
                <td class="path-cell">${renderPath(route)}</td>
                <td class="col-network"><span class="badge ${networkClass}">${networkVal}</span></td>
                <td class="col-tx-count">${route.count.toLocaleString()}</td>
                <td class="col-apr ${aprClass}">${aprDisplay}</td>
                <td class="col-volume font-bold">${formatUSD(route.volume)}</td>
                <td class="col-market-size">${formatUSD(route.market_size || 0)}</td>
                <td class="col-tvl">${tvlDisplay}</td>
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
                'FBTC': 'BTC',
                'WBNB': 'BNB'
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

        const toggleEl = document.getElementById('display-mode-toggle');
        const isAprMode = toggleEl ? toggleEl.checked : false;

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

        let html = '<div class="route-path-container">';

        tokens.forEach((token, idx) => {
            html += `
                <a href="${getCmcUrl(token)}" target="_blank" class="token-badge-link" onclick="event.stopPropagation();">
                    <span class="token-badge">${tokenIconHtml(token)} ${token}</span>
                </a>
            `;

            if (idx < tokens.length - 1) {
                const item = items[idx];
                let feeDisplay = '?';
                let aprDisplay = '';
                let protocolClass = '';
                let tooltip = '';
                let protocolName = 'Unknown';
                let networkName = '';

                if (item !== undefined && item !== null) {
                    if (typeof item === 'object') {
                        // Backend enriched object
                        const parsed = parseProtocol(item.fee);
                        let cleanFee = parsed.cleanFee;
                        protocolName = parsed.protocolName;
                        networkName = parsed.networkName;
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
                        feeDisplay = dispFee;

                        if (item.apr !== undefined && item.apr !== null && item.apr >= 0) {
                            const aprVal = item.apr * 100;
                            aprDisplay = formatAprPercent(aprVal);
                        }

                        tooltip = `APR: ${item.apr_str || 'N/A'}\nTier: ${cleanFee}\nProtocol: ${protocolName}\nNetwork: ${networkName || 'Ethereum'}`;
                    } else if (typeof item === 'string') {
                        const parsed = parseProtocol(item);
                        let cleanFee = parsed.cleanFee;
                        protocolName = parsed.protocolName;
                        networkName = parsed.networkName;
                        protocolClass = parsed.protocolClass;

                        let dispFee = cleanFee;
                        const parsedFee = parseFloat(cleanFee);
                        if (!isNaN(parsedFee) && parsedFee >= 5) {
                            dispFee = (parsedFee / 10000) + '%';
                            cleanFee = dispFee;
                        }
                        if (cleanFee.toLowerCase() === 'dynamic') {
                            feeDisplay = 'dyn';
                        } else {
                            feeDisplay = dispFee;
                        }
                        tooltip = `APR: N/A\nTier: ${cleanFee}\nProtocol: ${protocolName}\nNetwork: ${networkName || 'Ethereum'}`;
                    } else {
                        const feeNum = parseFloat(item);
                        if (!isNaN(feeNum)) {
                            feeDisplay = (feeNum / 10000) + '%';
                        }
                        tooltip = `APR: N/A\nTier: ${feeDisplay}\nProtocol: Unknown`;
                    }
                }

                let isClickable = false;
                let uniLinkHtml = '';
                let uniHref = '';
                let uniProtocol = '';

                if (item && typeof item === 'object' && item.pool_address) {
                    const parsed = parseProtocol(item.fee);
                    const protocolNameLower = parsed.protocolName.toLowerCase();
                    const networkLower = (parsed.networkName || 'ethereum').toLowerCase();
                    const pool_addr = item.pool_address;

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
                        isClickable = true;
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
                        isClickable = true;
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
                        isClickable = true;
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
                if (item && typeof item === 'object' && item.pool_address) {
                    const pool_addr = item.pool_address;
                    const parsed = parseProtocol(item.fee);
                    const protocolNameLower = parsed.protocolName.toLowerCase();
                    const networkLower = (parsed.networkName || 'ethereum').toLowerCase();

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

                // DexScreener chart link — reuses the same pool_address as the
                // Uniswap/PancakeSwap links; off by default via the data-lp toggle.
                let dexscreenerHtml = '';
                if (item && typeof item === 'object' && item.pool_address) {
                    const pool_addr = item.pool_address;
                    const parsed = parseProtocol(item.fee);
                    const networkLower = (parsed.networkName || 'ethereum').toLowerCase();
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

                // DeFi Llama yields link — UUID is looked up on the backend
                // (random slug, not derivable from the address); off by default.
                let defillamaHtml = '';
                if (item && typeof item === 'object' && item.defillama_uuid) {
                    const defillamaUrl = `https://defillama.com/yields/pool/${item.defillama_uuid}`;
                    defillamaHtml = `
                        <a href="${defillamaUrl}" target="_blank" class="lp-link defillama-link" data-tooltip="View on DeFi Llama" onclick="event.stopPropagation();">
                            <img src="/static/assets/defillama.ico" alt="DeFi Llama" class="lp-link-icon defillama-icon" style="border-radius: 50%;" />
                        </a>
                    `;
                }

                // Render both Fee display and APR display in separate text spans (or combine them)
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

                const arrowTooltip = `${protocolName}${networkName ? ' on ' + networkName : ''}`;

                // New layout: arrow spans full width with floating label on top
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
            } else if (key === 'tvl') {
                valA = getRouteTvl(a);
                valB = getRouteTvl(b);
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
        // Scope LP-element toggles to the routes body so they survive re-renders
        const lpScope = routesBody;
        checkboxes.forEach(cb => {
            const isVisible = cb.checked;

            // Table columns (data-col) — toggle header + cells
            if (cb.dataset.col) {
                const colClass = `col-${cb.dataset.col}`;
                document.querySelectorAll(`.${colClass}`).forEach(el => {
                    el.classList.toggle('hidden-column', !isVisible);
                });
            }

            // Liquidity-pool elements on the arrow (data-lp) — toggle via CSS hooks
            if (cb.dataset.lp) {
                lpScope.classList.toggle(`hide-lp-${cb.dataset.lp}`, !isVisible);
            }
        });
    };

    // Event listeners for sorting
    document.getElementById('sort-count').addEventListener('click', () => sortRoutes('count', 'sort-count'));
    document.getElementById('sort-apr').addEventListener('click', () => sortRoutes('apr', 'sort-apr'));
    document.getElementById('sort-vol').addEventListener('click', () => sortRoutes('volume', 'sort-vol'));
    document.getElementById('sort-mkt').addEventListener('click', () => sortRoutes('mkt', 'sort-mkt'));
    document.getElementById('sort-tvl').addEventListener('click', () => sortRoutes('tvl', 'sort-tvl'));
    document.getElementById('sort-avg').addEventListener('click', () => sortRoutes('avg', 'sort-avg'));
    document.getElementById('sort-pct').addEventListener('click', () => sortRoutes('pct', 'sort-pct'));

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

    const directOnlyCheckbox = document.getElementById('direct-only-filter');
    if (directOnlyCheckbox) {
        directOnlyCheckbox.addEventListener('change', () => {
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
