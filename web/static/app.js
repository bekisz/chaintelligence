const cmcSlugs = {
    'EURC': 'euro-coin',
    'USDC': 'usd-coin',
    'USDT': 'tether',
    'ETH': 'ethereum',
    'WETH': 'ethereum',
    'BTC': 'bitcoin',
    'WBTC': 'bitcoin',
    'DAI': 'multi-collateral-dai',
    'SOL': 'solana',
    'LINK': 'chainlink',
    'UNI': 'uniswap',
    'AAVE': 'aave',
    'OP': 'optimism-ethereum',
    'ARB': 'arbitrum',
    'MATIC': 'polygon',
    'POL': 'polygon',
    'BNB': 'bnb',
    'FDUSD': 'first-digital-usd',
    'PYUSD': 'paypal-usd',
    'USDE': 'ethena-usde',
    'USDP': 'paxos-standard',
    'LUSD': 'liquity-usd',
    'FRAX': 'frax',
    'GHO': 'gho',
    'CRVUSD': 'crvusd',
    'MIM': 'magic-internet-money',
    'TUSD': 'trueusd',
    'BUSD': 'binance-usd',
    'WUST': 'terrausd',
    'UST': 'terrausd',
    'USDY': 'ondo-us-dollar-yield',
    'USDM': 'mountain-protocol-usdm',
    'USDG': 'global-dollar-usdg',
};

let tokenSlugMap = {};

const getCmcUrl = (tokenSymbol) => {
    const symbol = (tokenSymbol || '').toUpperCase().trim();
    const slug = tokenSlugMap[symbol] || cmcSlugs[symbol] || symbol.toLowerCase();
    return `https://coinmarketcap.com/currencies/${slug}/`;
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

            // Sync the post-hoc network filter to the queried network
            // so the user can't filter to a network that wasn't queried.
            const posthocNetwork = document.getElementById('network-filter');
            if (posthocNetwork) {
                posthocNetwork.value = selectedNetwork;
                posthocNetwork.disabled = true;
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
        pct: 'desc'
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

                        if (item.apr !== undefined && item.apr !== null && item.apr > 0) {
                            const aprVal = item.apr * 100;
                            aprDisplay = aprVal < 0.1 ? aprVal.toFixed(3) + '%' : aprVal.toFixed(1) + '%';
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
                    const uniswapIconSvg = `<svg class="proto-brand-icon" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
                        <path d="M1.848 12.164a.823.823 0 0 1-.127-1.157L3.35 8.966a.823.823 0 0 1 1.284 1.026l-1.63 2.043a.823.823 0 0 1-1.156.129zm1.693-5.18l1.452.363a.823.823 0 0 1 .585.992.823.823 0 0 1-.992.585l-1.452-.363a.823.823 0 0 1 .407-1.577zm5.698-1.743c.454 0 .823.369.823.823v2.17c0 .454-.369.823-.823.823a.823.823 0 0 1-.823-.823v-2.17c0-.454.369-.823.823-.823zm2.463-3.86a.823.823 0 0 1 1.109-.297l1.884 1.088a.823.823 0 0 1-.823 1.425l-1.884-1.088a.823.823 0 0 1-.286-1.128zm10.15 10.78a9.49 9.49 0 0 1-5.704 8.708v-2.73a.823.823 0 0 0-.823-.823h-2.057a3.086 3.086 0 0 1-3.086-3.086v-.727a9.49 9.49 0 0 1 11.67-1.342z"/>
                    </svg>`;

                    // PancakeSwap bunny icon (CAKE cyan) - paths styled with currentColor/currentColor
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
                if (item && typeof item === 'object' && item.pool_address) {
                    const pool_addr = item.pool_address;
                    const parsed = parseProtocol(item.fee);
                    const protocolNameLower = parsed.protocolName.toLowerCase();
                    const networkLower = (parsed.networkName || 'ethereum').toLowerCase();

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
                if (uniLinkHtml || revertHtml) {
                    linksContent = `
                        <div class="label-pane links-pane">
                            ${uniLinkHtml}
                            ${revertHtml}
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
