let tokenSlugMap = {};

const getCmcUrl = (tokenSymbol) => {
    const symbol = (tokenSymbol || '').toUpperCase().trim();
    const slug = tokenSlugMap[symbol] || symbol.toLowerCase();
    return `https://coinmarketcap.com/currencies/${slug}/`;
};

const formatAprPercent = (pct) => {
    if (pct === null || pct === undefined || isNaN(pct)) return 'N/A';
    if (pct >= 10) return Math.round(pct) + '%';
    return Number(pct.toFixed(1)) + '%';
};

/**
 * Format a fee tier number (as a fraction, not percent) to at most 3 significant
 * digits after the first non-zero decimal digit.
 * e.g. 0.000069999... => "0.00352%", 0.003 => "0.3%", 0.05 => "5%"
 * Handles both fractional values (<1) and already-formatted percent strings.
 */
const formatFeeTier = (value) => {
    const num = typeof value === 'string' ? parseFloat(value) : value;
    if (isNaN(num)) return value;
    if (num === 0) return '0%';
    // Find position of first non-zero digit after decimal point
    const absNum = Math.abs(num);
    if (absNum >= 1) return num.toFixed(2).replace(/\.?0+$/, '') + '%';
    const magnitude = Math.floor(Math.log10(absNum)); // e.g. -3 for 0.001
    const decimalPlaces = -magnitude + 2; // 3 sig figs after first non-zero
    return parseFloat(num.toFixed(decimalPlaces)) + '%';
};

const getQueryDays = () => {
    const startDateInput = document.getElementById('start-date');
    const endDateInput = document.getElementById('end-date');
    let days = 1;
    const startStr = startDateInput ? startDateInput.value : '';
    const endStr = endDateInput ? endDateInput.value : '';
    if (startStr && endStr) {
        const s = new Date(startStr);
        const e = new Date(endStr);
        const diffTime = Math.abs(e - s);
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
        if (diffDays > 0) days = diffDays;
    }
    return days;
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

    function getYesterdayStr() {
        const d = new Date();
        d.setDate(d.getDate() - 1);
        return d.toISOString().split('T')[0];
    }

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
                const todayStr = new Date().toISOString().split('T')[0];
                endDateInput.value = maxDate === todayStr ? getYesterdayStr() : maxDate;

                // Set default start date to 6 days before end date (inclusive 7-day window)
                const endDate = new Date(endDateInput.value);
                const sevenDaysAgo = new Date(endDate);
                sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 6);
                const sevenDaysAgoStr = sevenDaysAgo.toISOString().split('T')[0];

                startDateInput.value = sevenDaysAgoStr >= dateRange.min_date ? sevenDaysAgoStr : dateRange.min_date;
            }
        } catch (error) {
            console.error('Error fetching date range:', error);
            // Fallback: just set end date to yesterday and start date to 6 days ago
            const yesterday = new Date();
            yesterday.setDate(yesterday.getDate() - 1);
            endDateInput.value = yesterday.toISOString().split('T')[0];
            const sevenDaysAgo = new Date(yesterday);
            sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 6);
            startDateInput.value = sevenDaysAgo.toISOString().split('T')[0];
        }
    };

    // Initial fetch with the current network filter value
    const queryNetworkSelect = document.getElementById('query-network-filter');

    // Set immediate defaults so the date inputs show real dates right away
    // (the API call below will refine them once it completes)
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const sevenDaysAgo = new Date(yesterday);
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 6);
    endDateInput.value = yesterday.toISOString().split('T')[0];
    startDateInput.value = sevenDaysAgo.toISOString().split('T')[0];

    fetchDateRange(queryNetworkSelect ? queryNetworkSelect.value : 'all');

    // Re-fetch when the network filter changes
    if (queryNetworkSelect) {
        queryNetworkSelect.addEventListener('change', () => {
            fetchDateRange(queryNetworkSelect.value);
        });
    }

    let allTokensList = [];
    let allFamiliesList = [];

    // Fetch official token logos & metadata from backend for lookahead autocomplete
    fetch('/api/coin/list')
        .then(response => response.json())
        .then(coins => {
            allTokensList = coins;
            coins.forEach(coin => {
                if (coin.symbol) {
                    const upperSymbol = coin.symbol.toUpperCase();
                    if (coin.image) tokenImageMap[upperSymbol] = coin.image;
                    if (coin.slug) tokenSlugMap[upperSymbol] = coin.slug;
                }
            });
        })
        .catch(error => {
            console.error('Error fetching token images:', error);
        });

    fetch('/api/coin-families')
        .then(res => res.json())
        .then(data => {
            const fams = data.families || {};
            allFamiliesList = Object.keys(fams).map(f => ({
                name: f,
                membersCount: fams[f]?.length || 0
            }));
        })
        .catch(err => console.error('Error fetching coin families:', err));

    const initTokenAutocomplete = (inputEl) => {
        if (!inputEl || inputEl.dataset.autocompleteInitialized) return;
        inputEl.dataset.autocompleteInitialized = 'true';
        inputEl.setAttribute('autocomplete', 'off');

        let container = inputEl.parentElement;
        if (!container.classList.contains('token-autocomplete-container')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'token-autocomplete-container';
            container.insertBefore(wrapper, inputEl);
            wrapper.appendChild(inputEl);
            container = wrapper;
        }

        const dropdown = document.createElement('div');
        dropdown.className = 'token-autocomplete-dropdown';
        container.appendChild(dropdown);

        const inputIcon = document.createElement('img');
        inputIcon.className = 'token-input-icon';
        container.appendChild(inputIcon);

        const updateInputIcon = () => {
            const sym = (inputEl.value || '').trim().toUpperCase();
            if (!sym || sym === '*') {
                inputIcon.style.display = 'none';
                inputEl.style.paddingLeft = '1rem';
                return;
            }
            const principal = getPrincipalSymbol(sym.replace('_YBA', ''));
            const iconSrc = tokenImageMap[sym] || tokenImageMap[principal] || tokenIconUrl(principal) || tokenIconUrl(sym);
            if (iconSrc) {
                inputIcon.src = iconSrc;
                inputIcon.onerror = () => {
                    inputIcon.src = '/static/favicon.png';
                };
                inputIcon.style.display = 'block';
                inputEl.style.paddingLeft = '2.3rem';
            } else {
                inputIcon.style.display = 'none';
                inputEl.style.paddingLeft = '1rem';
            }
        };

        let selectedIndex = -1;

        const renderDropdown = (query) => {
            updateInputIcon();
            const q = (query || '').trim().toUpperCase();
            dropdown.innerHTML = '';
            selectedIndex = -1;

            let matches = [];

            if (q === '' || q === '*') {
                matches.push({ type: 'wildcard', symbol: '*', name: 'Any Token (Wildcard)', icon: '/static/favicon.png' });
            }

            allFamiliesList.forEach(fam => {
                if (q === '' || fam.name.includes(q)) {
                    matches.push({
                        type: 'family',
                        symbol: fam.name,
                        name: `Family (${fam.membersCount} coins)`,
                        icon: getPrincipalSymbol(fam.name.replace('_YBA', ''))
                    });
                }
            });

            let coinMatches = [];
            allTokensList.forEach(coin => {
                const sym = (coin.symbol || '').toUpperCase();
                const name = coin.name || '';
                if (sym.includes(q) || name.toUpperCase().includes(q)) {
                    coinMatches.push({
                        type: 'coin',
                        symbol: sym,
                        name: name,
                        rank: coin.cmc_rank || 99999,
                        exact: sym === q
                    });
                }
            });

            coinMatches.sort((a, b) => {
                if (a.exact !== b.exact) return a.exact ? -1 : 1;
                return a.rank - b.rank;
            });

            matches = matches.concat(coinMatches.slice(0, 25));

            if (matches.length === 0) {
                dropdown.innerHTML = '<div style="padding: 10px; color: #94a3b8; font-size: 0.82rem; text-align: center;">No matching tokens</div>';
                dropdown.classList.add('active');
                return;
            }

            let html = '';
            let currentGroup = '';

            matches.forEach((item, idx) => {
                const groupName = item.type === 'wildcard' ? 'Wildcard' : (item.type === 'family' ? 'Coin Families' : 'Tokens');
                if (groupName !== currentGroup) {
                    currentGroup = groupName;
                    html += `<div class="token-autocomplete-group-title">${currentGroup}</div>`;
                }

                let iconSrc = item.icon;
                if (!iconSrc || item.type === 'coin' || item.type === 'family') {
                    const principal = getPrincipalSymbol(item.symbol);
                    iconSrc = tokenImageMap[item.symbol] || tokenImageMap[principal] || tokenIconUrl(principal) || tokenIconUrl(item.symbol);
                }

                html += `
                    <div class="token-autocomplete-item" data-index="${idx}" data-symbol="${item.symbol}">
                        <img src="${iconSrc}" onerror="this.src='/static/favicon.png'">
                        <div class="token-sym">${item.symbol}</div>
                        <div class="token-sub">${item.name}</div>
                    </div>
                `;
            });

            dropdown.innerHTML = html;
            dropdown.classList.add('active');

            dropdown.querySelectorAll('.token-autocomplete-item').forEach(el => {
                el.addEventListener('mousedown', (e) => {
                    e.preventDefault();
                    selectSymbol(el.dataset.symbol);
                });
            });
        };

        const selectSymbol = (sym) => {
            inputEl.value = sym;
            updateInputIcon();
            dropdown.classList.remove('active');
            inputEl.dispatchEvent(new Event('change', { bubbles: true }));
            inputEl.dispatchEvent(new Event('input', { bubbles: true }));
        };

        // Track whether the input was clicked while the dropdown was already open.
        // In that case, the upcoming focus event should close rather than re-open.
        let suppressNextOpen = false;

        inputEl.addEventListener('mousedown', () => {
            if (dropdown.classList.contains('active')) {
                suppressNextOpen = true;
            }
        });

        inputEl.addEventListener('focus', () => {
            if (suppressNextOpen) {
                suppressNextOpen = false;
                dropdown.classList.remove('active');
                return;
            }
            renderDropdown(inputEl.value);
        });

        inputEl.addEventListener('input', () => {
            updateInputIcon();
            renderDropdown(inputEl.value);
        });

        const updateSelection = (items) => {
            items.forEach((item, i) => {
                if (i === selectedIndex) {
                    item.classList.add('selected');
                    item.scrollIntoView({ block: 'nearest' });
                } else {
                    item.classList.remove('selected');
                }
            });
        };

        inputEl.addEventListener('keydown', (e) => {
            const items = dropdown.querySelectorAll('.token-autocomplete-item');
            if (!dropdown.classList.contains('active') || items.length === 0) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedIndex = (selectedIndex + 1) % items.length;
                updateSelection(items);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedIndex = (selectedIndex - 1 + items.length) % items.length;
                updateSelection(items);
            } else if (e.key === 'Enter') {
                if (selectedIndex >= 0 && selectedIndex < items.length) {
                    e.preventDefault();
                    selectSymbol(items[selectedIndex].dataset.symbol);
                }
            } else if (e.key === 'Escape') {
                dropdown.classList.remove('active');
            }
        });

        document.addEventListener('click', (e) => {
            if (!container.contains(e.target)) {
                dropdown.classList.remove('active');
            }
        });

        // Initialize icon for pre-populated values
        setTimeout(updateInputIcon, 200);
        setTimeout(updateInputIcon, 1000);
    };


    initTokenAutocomplete(startTokenInput);
    initTokenAutocomplete(endTokenInput);

    const initCustomChainSelector = (selectEl) => {
        if (!selectEl || selectEl.dataset.customSelectorInitialized) return;
        selectEl.dataset.customSelectorInitialized = 'true';

        const allChainsIcon = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='%23ff007a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolygon points='12 2 2 7 12 12 22 7 12 2'%3E%3C/polygon%3E%3Cpolyline points='2 17 12 22 22 17'%3E%3C/polyline%3E%3Cpolyline points='2 12 12 17 22 12'%3E%3C/polyline%3E%3C/svg%3E";

        const networkIcons = {
            'all': allChainsIcon,
            'Ethereum': 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/info/logo.png',
            'Arbitrum': 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/arbitrum/info/logo.png',
            'Base': 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/base/info/logo.png',
            'BNB': 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/binance/info/logo.png'
        };

        selectEl.style.display = 'none';

        const wrapper = document.createElement('div');
        wrapper.className = 'custom-network-container';
        selectEl.parentElement.insertBefore(wrapper, selectEl);
        wrapper.appendChild(selectEl);

        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'custom-network-trigger';
        wrapper.appendChild(trigger);

        const dropdown = document.createElement('div');
        dropdown.className = 'custom-network-dropdown';
        wrapper.appendChild(dropdown);

        const updateTrigger = () => {
            const val = selectEl.value || 'all';
            const selectedOpt = selectEl.options[selectEl.selectedIndex];
            const label = selectedOpt ? selectedOpt.text : 'All Chains';
            const iconSrc = networkIcons[val] || networkIcons['all'];

            trigger.innerHTML = `
                <img class="network-select-icon" src="${iconSrc}" onerror="this.src='/static/favicon.png'">
                <span class="network-select-label">${label}</span>
                <svg class="network-select-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
            `;
        };

        const renderDropdown = () => {
            let html = '';
            Array.from(selectEl.options).forEach(opt => {
                const val = opt.value;
                const label = opt.text;
                const iconSrc = networkIcons[val] || networkIcons['all'];
                const isSelected = val === selectEl.value;
                html += `
                    <div class="custom-network-option ${isSelected ? 'selected' : ''}" data-value="${val}">
                        <img src="${iconSrc}" onerror="this.src='/static/favicon.png'">
                        <span>${label}</span>
                    </div>
                `;
            });
            dropdown.innerHTML = html;

            dropdown.querySelectorAll('.custom-network-option').forEach(el => {
                el.addEventListener('click', () => {
                    selectEl.value = el.dataset.value;
                    updateTrigger();
                    dropdown.classList.remove('active');
                    selectEl.dispatchEvent(new Event('change', { bubbles: true }));
                });
            });
        };

        trigger.addEventListener('click', (e) => {
            e.stopPropagation();
            const isActive = dropdown.classList.contains('active');
            document.querySelectorAll('.custom-network-dropdown').forEach(d => d.classList.remove('active'));
            if (!isActive) {
                renderDropdown();
                dropdown.classList.add('active');
            }
        });

        document.addEventListener('click', (e) => {
            if (!wrapper.contains(e.target)) {
                dropdown.classList.remove('active');
            }
        });

        selectEl.updateCustomSelectorUI = updateTrigger;
        updateTrigger();
    };

    initCustomChainSelector(document.getElementById('query-network-filter'));
    initCustomChainSelector(document.getElementById('network-filter'));
    initCustomChainSelector(document.getElementById('undercut-network'));

    const getNetworkIconBadge = (netName) => {
        if (!netName) return '';
        const lower = netName.toLowerCase();
        let iconUrl = '/static/favicon.png';
        if (lower.includes('ethereum') || lower === 'eth') iconUrl = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/info/logo.png';
        else if (lower.includes('arbitrum') || lower === 'arb') iconUrl = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/arbitrum/info/logo.png';
        else if (lower.includes('base')) iconUrl = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/base/info/logo.png';
        else if (lower.includes('bnb') || lower.includes('binance') || lower === 'bsc') iconUrl = 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/binance/info/logo.png';

        return `<span class="network-icon-badge" title="${netName}"><img src="${iconUrl}" alt="${netName}" onerror="this.src='/static/favicon.png'"></span>`;
    };

    let symbolFamilyMap = {};
    let familySymbolsMap = {};

    const getTokenFamily = (sym) => {
        if (!sym) return null;
        const upper = sym.trim().toUpperCase();
        if (familySymbolsMap[upper]) {
            return upper;
        }
        if (symbolFamilyMap[upper]) {
            return symbolFamilyMap[upper];
        }
        if (upper.includes('USD') || upper.includes('DAI') || upper.includes('GHO') || upper.includes('FRAX')) return 'USD';
        if (upper.includes('ETH') || upper.includes('WETH')) return 'ETH';
        if (upper.includes('BTC') || upper.includes('WBTC')) return 'BTC';
        if (upper.includes('EUR')) return 'EUR';
        return upper;
    };

    const updateStableShortcutState = () => {
        const startToken = startTokenInput ? startTokenInput.value.trim() : '';
        const endToken = endTokenInput ? endTokenInput.value.trim() : '';
        const stableShortcutCheckbox = document.getElementById('stable-shortcut-filter');
        const stableShortcutWrapper = document.getElementById('stable-shortcut-wrapper');

        if (!startToken || !endToken) {
            if (stableShortcutCheckbox) {
                stableShortcutCheckbox.disabled = true;
                stableShortcutCheckbox.checked = false;
            }
            if (stableShortcutWrapper) {
                stableShortcutWrapper.style.opacity = '0.5';
                stableShortcutWrapper.title = 'Only enabled when querying tokens within the same family (e.g. USD-USD, ETH-ETH, BTC-BTC).';
            }
            return;
        }

        const famA = getTokenFamily(startToken);
        const famB = getTokenFamily(endToken);
        const isSameFamily = Boolean(famA && famB && famA === famB);

        if (stableShortcutCheckbox && stableShortcutWrapper) {
            if (isSameFamily) {
                stableShortcutCheckbox.disabled = false;
                stableShortcutWrapper.style.opacity = '1.0';
                stableShortcutWrapper.title = `Filter to routes with >1 hop where a middle token is not part of the ${famA} family.`;
            } else {
                stableShortcutCheckbox.disabled = true;
                stableShortcutCheckbox.checked = false;
                stableShortcutWrapper.style.opacity = '0.5';
                stableShortcutWrapper.title = `Only enabled when querying tokens within the same family (e.g. USD-USD, ETH-ETH, BTC-BTC). Currently queried: ${famA || startToken} - ${famB || endToken}.`;
            }
        }
    };

    fetch('/api/coin-families')
        .then(response => response.json())
        .then(data => {
            if (data && data.symbol_family_map) {
                symbolFamilyMap = data.symbol_family_map;
            }
            if (data && data.families) {
                familySymbolsMap = data.families;
            }
            updateStableShortcutState();
        })
        .catch(error => {
            console.error('Error fetching coin families:', error);
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

    const formatRelativeTime = (iso) => {
        if (!iso) return '-';
        const ts = new Date(iso).getTime();
        if (isNaN(ts)) return '-';
        const diffMs = Date.now() - ts;
        if (diffMs < 0) return 'now';
        const sec = Math.floor(diffMs / 1000);
        if (sec < 60) return `${sec}s ago`;
        const min = Math.floor(sec / 60);
        if (min < 60) return `${min}m ago`;
        const hr = Math.floor(min / 60);
        if (hr < 24) return `${hr}h ago`;
        const day = Math.floor(hr / 24);
        if (day < 30) return `${day}d ago`;
        const mo = Math.floor(day / 30);
        if (mo < 12) return `${mo}mo ago`;
        const yr = Math.floor(mo / 12);
        return `${yr}y ago`;
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

    const getRouteCid = (route) => {
        if (route.path_tokens) {
            for (let i = 0; i < route.path_tokens.length; i++) {
                if (i % 2 === 1 && typeof route.path_tokens[i] === 'object') {
                    return route.path_tokens[i].cid || 0;
                }
            }
        }
        return 0;
    };

    const getRoutePoolAddr = (route) => {
        if (route.path_tokens) {
            for (let i = 0; i < route.path_tokens.length; i++) {
                if (i % 2 === 1 && typeof route.path_tokens[i] === 'object') {
                    return route.path_tokens[i].pool_address || '';
                }
            }
        }
        return '';
    };

    const getRoutePoolId = (route) => {
        if (route.path_tokens) {
            for (let i = 0; i < route.path_tokens.length; i++) {
                if (i % 2 === 1 && typeof route.path_tokens[i] === 'object') {
                    return route.path_tokens[i].pool_id || route.path_tokens[i].pool_address || '';
                }
            }
        }
        return '';
    };

    const getProtocolColor = (proto) => {
        const p = (proto || '').toLowerCase();
        if (p.includes('uniswap v4') || p.includes('uniswap_v4') || p === 'v4') return '#9860f3';
        if (p.includes('uniswap v3') || p.includes('uniswap_v3') || p === 'v3') return '#ff007a';
        if (p.includes('pancakeswap v3') || p.includes('pancakeswap_v3')) return '#1fc7d4';
        if (p.includes('pancakeswap v4') || p.includes('pancakeswap_v4')) return '#4ade80';
        if (p.includes('uniswap v2') || p.includes('uniswap_v2')) return '#ff6ba6';
        if (p.includes('aerodrome')) return '#3b82f6';
        return '#627eea'; // fallback
    };

    const getRouteProtocol = (route) => {
        let protocols = new Set();
        if (route.path_tokens) {
            route.path_tokens.forEach((item, idx) => {
                if (idx % 2 === 1 && typeof item === 'object') {
                    const feeParts = (item.fee || '').split('|');
                    if (feeParts.length >= 2) {
                        protocols.add(feeParts[1].trim());
                    }
                }
            });
        }
        if (protocols.size === 0 && route.protocol) {
            protocols.add(route.protocol);
        }
        return protocols.size === 1 ? Array.from(protocols)[0] : '';
    };

    const filterAndRenderRoutes = () => {
        if (!currentRoutes) return;

        const minAprInput = document.getElementById('min-apr-filter');
        const minMktInput = document.getElementById('min-mkt-filter');
        const minTxsInput = document.getElementById('min-txs-filter');
        const acyclicCheckbox = document.getElementById('acyclic-filter');
        const directOnlyCheckbox = document.getElementById('direct-only-filter');
        const stableShortcutCheckbox = document.getElementById('stable-shortcut-filter');
        const networkFilter = document.getElementById('network-filter');
        const protocolFilter = document.getElementById('protocol-filter');

        const minAprVal = minAprInput ? parseFloat(minAprInput.value) || 0 : 0;
        const minMktVal = minMktInput ? parseFloat(minMktInput.value) || 0 : 0;
        const minTxsVal = minTxsInput ? parseInt(minTxsInput.value) || 0 : 0;
        const acyclicOnly = acyclicCheckbox ? acyclicCheckbox.checked : false;
        const directOnly = directOnlyCheckbox ? directOnlyCheckbox.checked : false;
        const stableShortcutOnly = stableShortcutCheckbox ? (stableShortcutCheckbox.checked && !stableShortcutCheckbox.disabled) : false;
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

            // Stable Pair Shortcut filter:
            // Keeps routes with >1 hop where at least one middle token is not part of the queried token family
            if (stableShortcutOnly) {
                let tokens = [];
                if (route.path_tokens) {
                    for (let i = 0; i < route.path_tokens.length; i += 2) {
                        tokens.push(route.path_tokens[i]);
                    }
                } else if (route.path) {
                    const parts = route.path.split(' ');
                    for (let i = 0; i < parts.length; i += 4) {
                        tokens.push(parts[i]);
                    }
                }
                // Must be multi-hop (> 1 hop => > 2 tokens)
                if (tokens.length <= 2) return false;

                const startToken = startTokenInput ? startTokenInput.value.trim() : '';
                const queriedFamily = getTokenFamily(startToken);

                // Check middle tokens (between start and end token)
                const middleTokens = tokens.slice(1, tokens.length - 1);
                const hasNonFamilyMiddleToken = middleTokens.some(t => getTokenFamily(t) !== queriedFamily);

                if (!hasNonFamilyMiddleToken) return false;
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
        const undercutPanel = document.getElementById('undercut-section');
        if (undercutPanel) {
            undercutPanel.classList.add('disabled');
            undercutPanel.classList.add('hidden');
        }
        const undercutResults = document.getElementById('undercut-results-section');
        if (undercutResults) undercutResults.classList.add('hidden');
        const ucNetworkSelect = document.getElementById('undercut-network');
        if (ucNetworkSelect) {
            ucNetworkSelect.innerHTML = '';
            ucNetworkSelect.disabled = true;
        }
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
            updateStableShortcutState();
            // Default sort: Daily fees descending for the selected time period
            if (currentRoutes && currentRoutes.length > 0) {
                sortRoutes('daily-fees', 'sort-daily-fees', 'desc');
            } else {
                filterAndRenderRoutes();
            }

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
            const undercutPanel = document.getElementById('undercut-section');
            if (undercutPanel) {
                undercutPanel.classList.remove('disabled');
                undercutPanel.classList.remove('hidden');
            }
            populateUndercutNetwork();
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

    const formatAddress = (addr) => {
        if (!addr) return '-';
        const isPoolId = addr.length > 42;
        const labelType = isPoolId ? 'Pool ID (PoolKey Hash)' : 'Contract Address';
        const shortened = addr.length > 13 ? `${addr.substring(0, 6)}...${addr.substring(addr.length - 4)}` : addr;
        return `<span class="monospace clickable-addr" title="${labelType}: ${addr}\nClick to copy" onclick="copyToClipboard('${addr}', this, event);">
            <span>${shortened}</span>
            <span class="copy-icon-wrapper">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
            </span>
        </span>`;
    };

    const renderRoutes = (routes) => {
        routesBody.innerHTML = '';
        const totalVolumeAllRoutes = currentRoutes.reduce((sum, r) => sum + (r.volume || 0), 0);
        routes.forEach((route, idx) => {
            let totalApr = 0;
            let hopCount = 0;
            let routeTvl = 0;
            let poolAddr = null;
            let poolId = null;
            let cid = null;
            let protocols = new Set();
            if (route.path_tokens) {
                route.path_tokens.forEach((item, idx) => {
                    if (idx % 2 === 1 && typeof item === 'object') {
                        totalApr += (item.apr || 0);
                        routeTvl += (item.tvl || 0);
                        poolAddr = item.pool_address;
                        poolId = item.pool_id || item.pool_address;
                        cid = item.cid;
                        hopCount++;
                        
                        const feeParts = (item.fee || '').split('|');
                        if (feeParts.length >= 2) {
                            protocols.add(feeParts[1].trim());
                        }
                    }
                });
            }
            if (protocols.size === 0 && route.protocol) {
                protocols.add(route.protocol);
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
            // Dead pool (no current liquidity) is greyed out but not excluded
            if (hopCount === 1 && (routeTvl <= 0)) {
                row.classList.add('dead-pool');
            }
            
            const cidDisplay = hopCount === 1 && cid !== null && cid !== undefined ? cid : '-';
            const poolIdDisplay = hopCount === 1 ? formatAddress(poolId) : '-';
            const poolAddrDisplay = hopCount === 1 ? formatAddress(poolAddr) : '-';
            
            const singleProtocol = protocols.size === 1 ? Array.from(protocols)[0] : null;
            const protocolDisplay = singleProtocol || '-';
            const protoColor = getProtocolColor(singleProtocol || route.protocol);

            let t0 = '', t1 = '';
            if (hopCount === 1 && route.path_tokens && route.path_tokens.length >= 3) {
                t0 = route.path_tokens[0];
                t1 = route.path_tokens[route.path_tokens.length - 1];
            }
            const today = new Date();
            const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
            const startDateVal = startDateInput ? startDateInput.value : '';

            const queryDays = getQueryDays();
            const dailyVolume = route.daily_volume !== undefined ? route.daily_volume : (route.volume / queryDays);
            const dailyFees = route.daily_fees !== undefined ? route.daily_fees : ((route.market_size || 0) / queryDays);

            const pctVol = totalVolumeAllRoutes > 0 ? (((route.volume || 0) / totalVolumeAllRoutes) * 100) : (route.pct_volume || 0);

            row.innerHTML = `
                <td class="path-cell">
                    ${renderPath(route)}
                    ${hopCount === 1 ? `
                        <div class="row-action-wrapper">
                            <button class="row-action-btn" title="Backtest LP Position" onclick="event.stopPropagation(); window.open('/backtester?token1=${encodeURIComponent(t0.toLowerCase())}&token2=${encodeURIComponent(t1.toLowerCase())}&apr=${(avgApr * 100).toFixed(2)}&start=${startDateVal}&end=${todayStr}', '_blank');">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
                                </svg>
                                Backtest
                            </button>
                        </div>
                    ` : ''}
                </td>
                <td class="col-cid">${cidDisplay}</td>
                <td class="col-pool-id hidden-column">${poolIdDisplay || poolAddrDisplay}</td>
                <td class="col-network">${getNetworkIconBadge(networkVal)}</td>
                <td class="col-protocol hidden-column font-bold" style="color: ${protoColor};">${protocolDisplay}</td>
                <td class="col-tx-count hidden-column">${route.count.toLocaleString()}</td>
                <td class="col-swaps hidden-column">${(route.swaps ?? route.count).toLocaleString()}</td>
                <td class="col-apr ${aprClass}">${aprDisplay}</td>
                <td class="col-volume hidden-column font-bold">${formatUSD(route.volume)}</td>
                <td class="col-market-size hidden-column">${formatUSD(route.market_size || 0)}</td>
                <td class="col-tvl">${tvlDisplay}</td>
                <td class="col-avg-volume">${formatUSD(dailyVolume)}</td>
                <td class="col-daily-fees">${formatUSD(dailyFees)}</td>
                <td class="col-pct-volume accent-text">${pctVol.toFixed(1)}%</td>
                <td class="col-last-activity hidden-column">${formatRelativeTime(route.last_activity)}</td>
            `;
            routesBody.appendChild(row);
        });
        updateColumnVisibility();
    };

    const tokenIconUrl = (symbol) => {
        const s = symbol.toLowerCase();
        return `https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25fa5/128/color/${s}.png`;
    };

    const getPrincipalSymbol = (symbol) => {
        if (!symbol) return '';
        let u = symbol.toUpperCase();
        
        const logoMappings = {
            'WETH': 'ETH', 'WBTC': 'BTC', 'CBBTC': 'BTC', 'TBTC': 'BTC',
            'KBTC': 'BTC', 'LBTC': 'BTC', 'FBTC': 'BTC', 'WBNB': 'BNB',
            'RETH': 'ETH', 'WSTETH': 'ETH', 'CBETH': 'ETH', 'EZETH': 'ETH',
            'WEETH': 'ETH', 'STETH': 'ETH', 'SYPUSDT': 'USDT'
        };
        if (logoMappings[u]) return logoMappings[u];

        let stripped = u;
        if (/^(AETH|AARB|ABAS|APOL|AOPT|CETH|CARB|CBAS|COPT|CPOL)/.test(stripped)) {
            stripped = stripped.replace(/^(AETH|AARB|ABAS|APOL|AOPT|CETH|CARB|CBAS|COPT|CPOL)/, '');
        } else if (/^(A|C|V|M)/.test(stripped) && stripped.length > 3) {
            stripped = stripped.replace(/^(A|C|V|M)/, '');
        }
        
        if (stripped === 'WSTE') stripped = 'WSTETH';
        if (stripped === 'CBE') stripped = 'CBETH';
        stripped = stripped.replace(/V[234]$/, '');

        if (logoMappings[stripped]) return logoMappings[stripped];
        return stripped;
    };

    const tokenIconHtml = (symbol, size = 16) => {
        const uppercaseSymbol = symbol.toUpperCase();
        
        // 1. Direct match from CoinGecko loaded map
        let url = tokenImageMap[uppercaseSymbol];
        
        if (!url) {
            // 2. Resolve yield-bearing/wrapped asset to principal token symbol
            const principal = getPrincipalSymbol(uppercaseSymbol);
            url = tokenImageMap[principal] || tokenIconUrl(principal) || tokenIconUrl(uppercaseSymbol);
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

        if (tokens.length === 2) {
            const getTokenHardness = (symbol) => {
                if (!symbol || typeof symbol !== 'string') return 0;
                const s = symbol.toUpperCase();
                if (s.includes('USD') || s === 'DAI' || s === 'MIM' || s === 'GHO' || s === 'FRAX') return 1000;
                if (s.includes('EUR')) return 930;
                if (s.includes('GOLD') || s === 'PAXG' || s === 'XAUT') return 850;
                if (s.includes('BTC') || s === 'WBTC' || s === 'CBBTC' || s === 'TBTC') return 870;
                if (s.includes('ETH') || s === 'WETH' || s === 'STETH') return 860;
                if (s.includes('SOL')) return 700;
                return 0;
            };
            const h0 = getTokenHardness(tokens[0]);
            const h1 = getTokenHardness(tokens[1]);
            if (h0 > h1) {
                tokens = [tokens[1], tokens[0]];
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
                            // bps value — convert to percent fraction then format
                            dispFee = formatFeeTier(parsedFee / 10000);
                            cleanFee = dispFee;
                        } else if (!isNaN(parsedFee) && cleanFee.includes('%')) {
                            // Already a percent string — re-format to trim long decimals
                            dispFee = formatFeeTier(parsedFee);
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
                            dispFee = formatFeeTier(parsedFee / 10000);
                            cleanFee = dispFee;
                        } else if (!isNaN(parsedFee) && cleanFee.includes('%')) {
                            dispFee = formatFeeTier(parsedFee);
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
                            feeDisplay = formatFeeTier(feeNum / 10000);
                        }
                        tooltip = `APR: N/A\nTier: ${feeDisplay}\nProtocol: Unknown`;
                    }
                }

                let isClickable = false;
                let uniLinkHtml = '';
                let revertHtml = '';
                let dexscreenerHtml = '';
                let defillamaHtml = '';
                let explorerHtml = '';
                let geckoterminalHtml = '';
                let definedHtml = '';

                const links = (item && typeof item === 'object' && item.links) || {};
                if (links.pancakeswap) {
                    uniLinkHtml = `<a href="${links.pancakeswap}" target="_blank" class="pool-label-link pool-label-link--pancakeswap" data-tooltip="View on PancakeSwap" onclick="event.stopPropagation();"><svg class="proto-brand-icon" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><ellipse cx="50" cy="72" rx="26" ry="18" fill="#d1884f" opacity="0.9"/><ellipse cx="50" cy="68" rx="22" ry="14" fill="#d1884f"/><ellipse cx="36" cy="32" rx="8" ry="20" fill="#d1884f" opacity="0.9" transform="rotate(-15 36 32)"/><ellipse cx="64" cy="32" rx="8" ry="20" fill="#d1884f" opacity="0.9" transform="rotate(15 64 32)"/><ellipse cx="50" cy="58" rx="22" ry="20" fill="#d1884f"/><ellipse cx="50" cy="60" rx="18" ry="16" fill="#d1884f"/><circle cx="42" cy="55" r="3.5" fill="none" stroke="#ffffff" stroke-width="2"/><circle cx="58" cy="55" r="3.5" fill="none" stroke="#ffffff" stroke-width="2"/></svg></a>`;
                    isClickable = true;
                } else if (links.uniswap) {
                    uniLinkHtml = `<a href="${links.uniswap}" target="_blank" class="pool-label-link pool-label-link--uniswap" data-tooltip="View on Uniswap" onclick="event.stopPropagation();"><svg class="proto-brand-icon" viewBox="0 0 438 504" fill="#FF007A" xmlns="http://www.w3.org/2000/svg"><path d="M171.43,114.54c-5.45-.78-5.71-1-3.12-1.3,4.94-.78,16.37.26,24.42,2.08,18.7,4.41,35.58,15.84,53.5,35.84l4.68,5.46,6.75-1c28.83-4.68,58.44-1,83.11,10.39,6.76,3.11,17.41,9.35,18.7,10.9.52.52,1.3,3.9,1.82,7.28,1.82,12.2,1,21.29-2.85,28.31-2.08,3.89-2.08,4.93-.78,8.31a7.79,7.79,0,0,0,7,4.41c6.23,0,12.73-9.87,15.84-23.63l1.3-5.46,2.34,2.6c13.24,14.81,23.63,35.32,25.19,49.87l.52,3.89-2.34-3.37c-3.89-6-7.53-9.87-12.46-13.25-8.83-6-18.18-7.79-42.86-9.09-22.33-1.3-35.06-3.11-47.53-7.27-21.3-7-32.2-16.1-57.4-49.61-11.17-14.8-18.18-22.85-25.19-29.61C206.75,125.45,191.43,117.66,171.43,114.54Z"/><path d="M364.93,147.53c.52-9.87,1.82-16.37,4.67-22.34,1-2.34,2.08-4.42,2.34-4.42s-.26,1.82-1,3.9c-2.08,5.71-2.34,13.76-1,22.86,1.82,11.68,2.6,13.24,15.07,26,5.71,6,12.46,13.5,15.06,16.62l4.42,5.71L400,191.68c-5.45-5.2-17.92-15.07-20.78-16.36-1.81-1-2.07-1-3.37.26-1,1-1.3,2.59-1.3,10.12-.26,11.69-1.82,19-5.72,26.5-2.07,3.89-2.33,3.11-.52-1.3,1.3-3.38,1.56-4.94,1.56-16.1,0-22.6-2.59-28.06-18.44-37.15-3.89-2.33-10.65-5.71-14.54-7.53a57.93,57.93,0,0,1-7-3.37c.51-.52,15.84,3.89,21.81,6.49,9.09,3.64,10.65,3.89,11.69,3.64C364.15,156.1,364.67,154,364.93,147.53Z"/><path d="M182.08,186.22c-10.91-15.06-17.92-38.44-16.36-55.84l.52-5.45,2.59.52a60.93,60.93,0,0,1,16.63,6.23c10.39,6.24,15.06,14.81,19.48,36.1,1.29,6.24,3.11,13.51,3.89,15.85,1.3,3.89,6.24,13,10.39,18.7,2.86,4.15,1,6.23-5.45,5.71C203.9,207,190.65,197.91,182.08,186.22Z"/><path d="M351.68,299.21c-51.42-20.78-69.6-38.7-69.6-69.09,0-4.42.25-8.05.25-8.05a49.86,49.86,0,0,1,4.42,3.37c10.39,8.31,22.08,11.95,54.54,16.63,19,2.85,29.87,4.93,39.74,8.31,31.43,10.39,50.91,31.68,55.58,60.51,1.3,8.31.52,24.16-1.56,32.47-1.81,6.49-7,18.44-8.31,18.7-.26,0-.78-1.3-.78-3.38-.52-10.91-6-21.29-15.06-29.35C400,320,386,313,351.68,299.21Z"/><path d="M315.32,307.78a61.45,61.45,0,0,0-2.6-10.91l-1.3-3.9,2.34,2.86c3.38,3.9,6,8.57,8.31,15.06,1.82,4.94,1.82,6.5,1.82,14.55,0,7.79-.26,9.61-1.82,14a46.86,46.86,0,0,1-10.91,17.41c-9.35,9.61-21.55,14.8-39,17.14-3.12.26-11.95,1-19.74,1.56-19.48,1-32.47,3.11-44.16,7.27-1.56.52-3.11,1-3.37.78-.52-.52,7.53-5.2,14-8.31,9.09-4.42,18.44-6.76,39-10.39,10.13-1.56,20.52-3.64,23.12-4.68C306.75,352.19,319.48,332.19,315.32,307.78Z"/><path d="M339,349.59q-10.14-22.2-4.68-42.07c.52-1.3,1-2.6,1.56-2.6a11.07,11.07,0,0,1,3.63,1.82c3.12,2.08,9.61,5.71,26.24,14.8,21,11.43,33,20.26,41.29,30.39,7.28,8.83,11.69,19,13.77,31.43,1.3,7,.52,23.89-1.3,30.9-5.71,22.08-18.7,39.74-37.66,49.87a36.28,36.28,0,0,1-5.45,2.6c-.26,0,.78-2.6,2.33-5.71,6.24-13.25,7-26,2.34-40.26-2.86-8.83-8.83-19.48-20.78-37.4C346,362.58,342.59,357.12,339,349.59Z"/><path d="M145.46,429.07c19.22-16.1,42.85-27.53,64.67-31.17,9.35-1.56,24.93-1,33.51,1.3,13.76,3.64,26.23,11.43,32.72,21,6.23,9.35,9.09,17.4,11.95,35.32,1,7,2.34,14.29,2.6,15.84,2.07,9.36,6.23,16.63,11.42,20.52,8.06,6,22.08,6.24,35.85,1,2.33-.78,4.41-1.56,4.41-1.3.52.52-6.49,5.2-11.17,7.54a36.81,36.81,0,0,1-18.7,4.41c-12.46,0-23.11-6.49-31.68-19.48-1.82-2.6-5.46-10.13-8.57-17.14-9.1-21-13.77-27.27-24.42-34.28-9.35-6-21.3-7.28-30.39-2.86-11.94,5.71-15.06,21-6.75,30.39,3.38,3.89,9.61,7,14.8,7.79a15.86,15.86,0,0,0,17.93-15.85c0-6.23-2.34-9.86-8.58-12.72-8.31-3.64-17.4.52-17.14,8.57,0,3.38,1.56,5.45,4.94,7,2.08,1,2.08,1,.52.78-7.54-1.56-9.35-10.91-3.38-16.88,7.27-7.27,22.6-4.16,27.79,6,2.08,4.16,2.34,12.47.52,17.66C243.9,474,231.43,480,218.7,476.6c-8.57-2.34-12.21-4.68-22.59-15.32-18.19-18.7-25.2-22.34-51.17-26.24l-4.94-.78Z"/><path fill-rule="evenodd" d="M8.84,11.17C69.36,84.67,162.6,199,167.28,205.18c3.89,5.2,2.33,10.13-4.16,13.77-3.64,2.08-11.17,4.16-14.8,4.16a18.74,18.74,0,0,1-12.47-5.46c-2.34-2.34-12.47-17.14-35.32-52.72-17.41-27.27-32.21-49.87-32.47-50.13-1-.52-1-.52,30.65,56.1,20,35.58,26.49,48.31,26.49,49.87,0,3.37-1,5.19-5.19,9.87-7,7.79-10.13,16.62-12.47,35.06-2.6,20.52-9.61,35.06-29.61,59.74C66.24,340,64.42,342.58,61.57,348.55c-3.64,7.28-4.68,11.43-5.2,20.78-.52,9.87.52,16.11,3.38,25.46,2.6,8.31,5.45,13.76,12.47,24.41,6,9.35,9.61,16.36,9.61,19,0,2.08.52,2.08,9.87,0,22.33-5.19,40.77-14,50.9-24.93,6.24-6.76,7.79-10.39,7.79-19.74,0-6-.26-7.28-1.81-10.91-2.6-5.72-7.54-10.39-18.19-17.66-14-9.61-20-17.41-21.55-27.79-1.3-8.83.26-14.81,8-31.17,8-16.88,10.13-23.9,11.43-41,.78-10.91,2.07-15.32,5.19-18.7,3.38-3.63,6.23-4.93,14.29-6,13.24-1.82,21.81-5.2,28.57-11.69,6-5.45,8.57-10.91,8.83-19l.26-6L182.08,200C169.87,186,.79,0,0,0-.25,0,3.91,4.93,8.84,11.17ZM88.58,380.5a10.71,10.71,0,0,0-3.38-14.28C80.79,363.36,74,364.66,74,368.55a2.65,2.65,0,0,0,2.08,2.6c2.34,1.3,2.6,2.6.78,5.45s-1.82,5.46.52,7.28C81.05,386.73,86,385.18,88.58,380.5Z"/><path fill-rule="evenodd" d="M193.77,243.88c-6.24,1.82-12.21,8.57-14,15.33-1,4.15-.52,11.69,1.3,14,2.86,3.64,5.46,4.68,12.73,4.68,14.28,0,26.49-6.24,27.79-13.77,1.3-6.23-4.16-14.8-11.69-18.7C206,243.36,197.92,242.59,193.77,243.88Zm16.62,13c2.08-3.12,1.3-6.49-2.6-8.83-7-4.42-17.66-.78-17.66,6,0,3.38,5.46,7,10.65,7C204.16,261,208.83,259,210.39,256.87Z"/></svg></a>`;
                    isClickable = true;
                }
                if (links.revert) {
                    revertHtml = `<a href="${links.revert}" target="_blank" class="lp-link revert-link" data-tooltip="View on Revert Finance" onclick="event.stopPropagation();"><img src="/static/assets/revert.svg" alt="Revert Finance" class="lp-link-icon revert-icon" /></a>`;
                }
                if (links.dexscreener) {
                    dexscreenerHtml = `<a href="${links.dexscreener}" target="_blank" class="lp-link dexscreener-link" data-tooltip="View on DexScreener" onclick="event.stopPropagation();"><img src="/static/assets/dexscreener.ico" alt="DexScreener" class="lp-link-icon dexscreener-icon" style="border-radius: 50%;" /></a>`;
                }
                if (links.defillama) {
                    defillamaHtml = `<a href="${links.defillama}" target="_blank" class="lp-link defillama-link" data-tooltip="View on DeFi Llama" onclick="event.stopPropagation();"><img src="/static/assets/defillama.ico" alt="DeFi Llama" class="lp-link-icon defillama-icon" style="border-radius: 50%;" /></a>`;
                }
                if (links.explorer) {
                    explorerHtml = `<a href="${links.explorer}" target="_blank" class="lp-link explorer-link" data-tooltip="View on Block Explorer" onclick="event.stopPropagation();"><img src="/static/assets/explorer.ico" alt="Explorer" class="lp-link-icon explorer-icon" style="border-radius: 50%;" /></a>`;
                }
                if (links.geckoterminal) {
                    geckoterminalHtml = `<a href="${links.geckoterminal}" target="_blank" class="lp-link geckoterminal-link" data-tooltip="View on GeckoTerminal" onclick="event.stopPropagation();"><img src="/static/assets/geckoterminal.ico" alt="GeckoTerminal" class="lp-link-icon geckoterminal-icon" style="border-radius: 50%;" /></a>`;
                }
                if (links.defined) {
                    definedHtml = `<a href="${links.defined}" target="_blank" class="lp-link defined-link" data-tooltip="View on Defined.fi" onclick="event.stopPropagation();"><img src="/static/assets/defined.ico" alt="Defined.fi" class="lp-link-icon defined-icon" style="border-radius: 50%;" /></a>`;
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
                if (uniLinkHtml || revertHtml || dexscreenerHtml || defillamaHtml || explorerHtml || geckoterminalHtml || definedHtml) {
                    linksContent = `
                        <div class="label-pane links-pane">
                            ${uniLinkHtml}
                            ${revertHtml}
                            ${dexscreenerHtml}
                            ${defillamaHtml}
                            ${explorerHtml}
                            ${geckoterminalHtml}
                            ${definedHtml}
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

    const sortRoutes = (key, headerId, forceDir = null) => {
        if (!currentRoutes || currentRoutes.length === 0) return;

        // Toggle direction (unless an explicit direction is requested)
        sortDirection[key] = forceDir || (sortDirection[key] === 'desc' ? 'asc' : 'desc');
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
            } else if (key === 'swaps') {
                valA = a.swaps ?? a.count;
                valB = b.swaps ?? b.count;
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
                const days = getQueryDays();
                valA = a.daily_volume !== undefined ? a.daily_volume : (a.volume / days);
                valB = b.daily_volume !== undefined ? b.daily_volume : (b.volume / days);
            } else if (key === 'daily-fees') {
                const days = getQueryDays();
                valA = a.daily_fees !== undefined ? a.daily_fees : ((a.market_size || 0) / days);
                valB = b.daily_fees !== undefined ? b.daily_fees : ((b.market_size || 0) / days);
            } else if (key === 'pct') {
                valA = a.pct_volume;
                valB = b.pct_volume;
            } else if (key === 'last-activity') {
                valA = a.last_activity ? new Date(a.last_activity).getTime() : 0;
                valB = b.last_activity ? new Date(b.last_activity).getTime() : 0;
            } else if (key === 'cid') {
                valA = getRouteCid(a);
                valB = getRouteCid(b);
            } else if (key === 'pool_addr') {
                valA = getRoutePoolAddr(a);
                valB = getRoutePoolAddr(b);
                return dir === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
            } else if (key === 'pool_id') {
                valA = getRoutePoolId(a);
                valB = getRoutePoolId(b);
                return dir === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
            } else if (key === 'network') {
                valA = a.network || '';
                valB = b.network || '';
                return dir === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
            } else if (key === 'protocol') {
                valA = getRouteProtocol(a) || '';
                valB = getRouteProtocol(b) || '';
                return dir === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
            }

            return dir === 'asc' ? valA - valB : valB - valA;
        });

        filterAndRenderRoutes();
    };

    const updateColumnVisibility = () => {
        const checkboxes = document.querySelectorAll('#column-selector-dropdown input[type="checkbox"], #lp-options-dropdown input[type="checkbox"], #table-columns-dropdown input[type="checkbox"]');
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
                const lpClass = `hide-lp-${cb.dataset.lp}`;
                if (routesBody) routesBody.classList.toggle(lpClass, !isVisible);
                document.body.classList.toggle(lpClass, !isVisible);
            }
        });

        // Undercut table columns (data-uc-col) — toggle header + cells
        document.querySelectorAll('#uc-columns-dropdown input[type="checkbox"]').forEach(cb => {
            if (cb.dataset.ucCol) {
                const ucColClass = `uc-col-${cb.dataset.ucCol}`;
                document.querySelectorAll(`.${ucColClass}`).forEach(el => {
                    el.classList.toggle('hidden-column', !cb.checked);
                });
            }
        });
    };

    // Event listeners for sorting
    document.getElementById('sort-count').addEventListener('click', () => sortRoutes('count', 'sort-count'));
    document.getElementById('sort-swaps').addEventListener('click', () => sortRoutes('swaps', 'sort-swaps'));
    document.getElementById('sort-apr').addEventListener('click', () => sortRoutes('apr', 'sort-apr'));
    document.getElementById('sort-vol').addEventListener('click', () => sortRoutes('volume', 'sort-vol'));
    document.getElementById('sort-mkt').addEventListener('click', () => sortRoutes('mkt', 'sort-mkt'));
    document.getElementById('sort-tvl').addEventListener('click', () => sortRoutes('tvl', 'sort-tvl'));
    document.getElementById('sort-avg').addEventListener('click', () => sortRoutes('avg', 'sort-avg'));
    const sortDailyFeesEl = document.getElementById('sort-daily-fees');
    if (sortDailyFeesEl) sortDailyFeesEl.addEventListener('click', () => sortRoutes('daily-fees', 'sort-daily-fees'));
    document.getElementById('sort-pct').addEventListener('click', () => sortRoutes('pct', 'sort-pct'));
    const sortLastActivityEl = document.getElementById('sort-last-activity');
    if (sortLastActivityEl) sortLastActivityEl.addEventListener('click', () => sortRoutes('last-activity', 'sort-last-activity'));
    const sortPoolAddrEl = document.getElementById('sort-pool-addr');
    if (sortPoolAddrEl) sortPoolAddrEl.addEventListener('click', () => sortRoutes('pool_addr', 'sort-pool-addr'));
    const sortPoolIdEl = document.getElementById('sort-pool-id');
    if (sortPoolIdEl) sortPoolIdEl.addEventListener('click', () => sortRoutes('pool_id', 'sort-pool-id'));
    document.getElementById('sort-network').addEventListener('click', () => sortRoutes('network', 'sort-network'));
    document.getElementById('sort-protocol').addEventListener('click', () => sortRoutes('protocol', 'sort-protocol'));

    analyzeBtn.addEventListener('click', performAnalysis);

    // Collapse / expand the swap results table
    const collapseResultsBtn = document.getElementById('collapse-results-btn');
    const routesTableResponsive = document.querySelector('#results-section .table-responsive');
    if (collapseResultsBtn && routesTableResponsive) {
        collapseResultsBtn.addEventListener('click', () => {
            routesTableResponsive.classList.toggle('collapsed');
        });
    }

    // Collapse / expand the undercut results table
    const collapseUndercutBtn = document.getElementById('collapse-undercut-btn');
    const undercutTableResponsive = document.getElementById('undercut-table-responsive');
    if (collapseUndercutBtn && undercutTableResponsive) {
        collapseUndercutBtn.addEventListener('click', () => {
            undercutTableResponsive.classList.toggle('collapsed');
        });
    }

    // Undercut backtest: simulate a hypothetical extra pool for the pair
    let currentUndercutData = null;

    const populateUndercutNetwork = () => {
        const select = document.getElementById('undercut-network');
        if (!select) return;
        const networks = new Set();
        (currentRoutes || []).forEach(route => {
            const net = route.network || 'Ethereum';
            let protocols = new Set();
            if (route.path_tokens) {
                route.path_tokens.forEach((item, idx) => {
                    if (idx % 2 === 1 && typeof item === 'object' && item.fee) {
                        const parts = item.fee.split('|');
                        if (parts.length >= 2) protocols.add(parts[1].trim());
                    }
                });
            }
            if (protocols.size === 0 && route.protocol) protocols.add(route.protocol);
            // Only Uniswap-kind pools are allowed at the backtest
            const hasUniswap = [...protocols].some(p => p && p.toLowerCase().startsWith('uniswap'));
            if (hasUniswap) networks.add(net);
        });
        const prev = select.value;
        select.innerHTML = '';
        [...networks].sort().forEach(net => {
            const opt = document.createElement('option');
            opt.value = net;
            opt.textContent = net;
            select.appendChild(opt);
        });
        if (networks.has(prev)) {
            select.value = prev;
        } else {
            select.selectedIndex = networks.size > 0 ? 0 : -1;
        }
        select.disabled = networks.size === 0;
        if (select.updateCustomSelectorUI) select.updateCustomSelectorUI();
    };

    const renderUndercut = (data) => {
        currentUndercutData = data;
        updateUndercutDefaultFee(data);
        filterAndRenderUndercut();
    };

    const updateUndercutDefaultFee = (data) => {
        const feeInput = document.getElementById('undercut-fee');
        const pools = (data && data.pools) || [];
        // Only pools with real current liquidity are competitors: dead pools
        // (tvl = 0) carry huge historical volume at low fees and would drag the
        // volume-weighted average down to near zero. Also drop near-dead pools
        // whose TVL is negligible relative to the market leader, so the default
        // fee undercuts the pool(s) that actually route the pair's traffic.
        const live = pools.filter(p => (p.tvl || 0) > 0);
        if (live.length === 0) return;
        const maxTvl = Math.max(...live.map(p => p.tvl));
        const competitors = live.filter(p => p.tvl >= 0.2 * maxTvl);
        if (competitors.length === 0) return;
        let volSum = 0;
        let feeVolSum = 0;
        competitors.forEach(p => {
            const vol = p.volume || 0;
            const feeBps = p.fee_bps || 0;
            if (vol > 0 && feeBps > 0) {
                volSum += vol;
                feeVolSum += feeBps * vol;
            }
        });
        if (volSum <= 0 || feeVolSum <= 0) return;
        const vwapBps = feeVolSum / volSum;
        let recBps = vwapBps * 0.9;
        recBps = Math.round(recBps / 5) * 5;
        recBps = Math.max(1, Math.min(500, recBps));
        if (feeInput) feeInput.value = (recBps / 100).toFixed(2);
    };

    const filterAndRenderUndercut = () => {
        const data = currentUndercutData;
        const body = document.getElementById('undercut-body');
        if (!data || !body) return;
        body.innerHTML = '';
        const t0 = data.start_token;
        const t1 = data.end_token;
        const network = data.network || 'Ethereum';
        const days = data.days || 1;

        const minAprVal = parseFloat(document.getElementById('uc-min-apr-filter')?.value) || 0;
        const minMktVal = parseFloat(document.getElementById('uc-min-mkt-filter')?.value) || 0;
        const minTxsVal = parseInt(document.getElementById('uc-min-txs-filter')?.value) || 0;
        const ucProtocol = document.getElementById('uc-protocol-filter')?.value || 'all';

        const hyp = data.hypothetical;
        const pools = data.pools || [];
        const totalVol = data.total_volume || 0;

        const buildPathTokens = (feeDisplay, protocolName, aprPct) => ({
            path_tokens: [t0, {
                fee: `${feeDisplay}|${protocolName}|${network}`,
                apr: aprPct / 100,
                apr_str: formatAprPercent(aprPct)
            }, t1]
        });
        const appendRow = (pathTokens, cells, isHypothetical, protocolName) => {
            const row = document.createElement('tr');
            row.classList.add('fade-in');
            let tooltip = null;
            if (isHypothetical) {
                row.classList.add('hypothetical-row');
                tooltip = 'Hypothetical Pool';
            } else if (cells.tvl === null || cells.tvl === undefined || cells.tvl <= 0) {
                // Dead pool (no current liquidity): grey out but do not exclude
                row.classList.add('dead-pool');
            } else if (cells.realApr != null && cells.apr != null) {
                const dApr = cells.apr - cells.realApr;
                const dTx = cells.count - cells.realCount;
                const dVol = cells.volume - cells.realVolume;
                const parts = [];
                if (cells.realCount !== undefined && cells.realCount != null) parts.push(`TXs ${cells.realCount} → ${cells.count}${dTx > 0 ? ` (+${dTx})` : dTx < 0 ? ` (${dTx})` : ''}`);
                if (cells.realSwaps !== undefined && cells.realSwaps != null) parts.push(`Swaps ${cells.realSwaps} → ${cells.swaps}`);
                if (cells.realApr != null && cells.realApr !== undefined) parts.push(`APR ${formatAprPercent(cells.realApr)} → ${formatAprPercent(cells.apr)} (${dApr >= 0 ? '+' : ''}${dApr.toFixed(2)}%)`);
                if (cells.realVolume !== undefined) parts.push(`Vol ${formatUSD(cells.realVolume)} → ${formatUSD(cells.volume)} (${dVol >= 0 ? '+' : ''}${formatUSD(dVol)})`);
                tooltip = parts.join(' · ');
            }
            const tooltipAttr = tooltip ? ` title="${tooltip.replace(/"/g, '&quot;')}"` : '';

            const aprClass = cells.apr > 0.5 ? 'text-success font-bold' : (cells.apr > 0 ? 'text-success' : 'text-muted');
            const tvlDisplay = cells.tvl !== null && cells.tvl !== undefined ? formatUSD(cells.tvl) : '-';

            // For the hypothetical pool: CID shows "N/A", Pool Address / ID is left empty
            const cidDisplay = isHypothetical
                ? 'N/A'
                : (cells.cid !== null && cells.cid !== undefined ? cells.cid : '-');
            const poolIdDisplay = isHypothetical ? '' : (cells.poolId || '');
            const protocolDisplay = protocolName || (isHypothetical ? 'Uniswap V4' : 'Uniswap V3');

            row.innerHTML = `
                <td class="path-cell"${tooltipAttr}>${renderPath(pathTokens)}</td>
                <td class="uc-col-cid"${tooltipAttr}>${cidDisplay}</td>
                <td class="uc-col-pool-id hidden-column monospace"${tooltipAttr}>${poolIdDisplay}</td>
                <td class="uc-col-network"${tooltipAttr}>${getNetworkIconBadge(network)}</td>
                <td class="uc-col-protocol hidden-column font-bold"${tooltipAttr}>${protocolDisplay}</td>
                <td class="uc-col-tx-count hidden-column"${tooltipAttr}>${cells.count.toLocaleString()}</td>
                <td class="uc-col-swaps hidden-column"${tooltipAttr}>${(cells.swaps ?? cells.count).toLocaleString()}</td>
                <td class="uc-col-apr ${aprClass}"${tooltipAttr}>${formatAprPercent(cells.apr)}</td>
                <td class="uc-col-volume hidden-column font-bold"${tooltipAttr}>${formatUSD(cells.volume)}</td>
                <td class="uc-col-market-size hidden-column"${tooltipAttr}>${formatUSD(cells.fees)}</td>
                <td class="uc-col-tvl"${tooltipAttr}>${tvlDisplay}</td>
                <td class="uc-col-avg-volume"${tooltipAttr}>${formatUSD(cells.volume / days)}</td>
                <td class="uc-col-daily-fees"${tooltipAttr}>${formatUSD(cells.fees / days)}</td>
                <td class="uc-col-pct-volume hidden-column accent-text"${tooltipAttr}>${cells.pct.toFixed(1)}%</td>
                <td class="uc-col-last-activity hidden-column"${tooltipAttr}>${formatRelativeTime(cells.lastActivity)}</td>
            `;
            body.appendChild(row);
        };

        const rowSpecs = [];
        // Hypothetical pool first, highlighted with a different background
        if (hyp) {
            rowSpecs.push({
                pathTokens: buildPathTokens(hyp.fee_display, 'Uniswap V4', hyp.apr_pct),
                cells: {
                    count: hyp.diverted_count,
                    swaps: hyp.swaps ?? hyp.diverted_count,
                    apr: hyp.apr_pct,
                    volume: hyp.diverted_volume,
                    fees: hyp.fee_usd,
                    tvl: hyp.liquidity_usd,
                    pct: hyp.diverted_pct
                },
                isHypothetical: true,
                network: network,
                protocol: 'Uniswap V4'
            });
        }

        // Align backtest pools with the active routes from the top table (N active pools -> N+1 backtest rows)
        const activeRouteCids = new Set();
        const activeRouteAddrs = new Set();
        const activeRouteKeys = new Set();
        (currentRoutes || []).forEach(route => {
            if (route.path_tokens) {
                route.path_tokens.forEach((item, idx) => {
                    if (idx % 2 === 1 && typeof item === 'object' && item !== null) {
                        if (item.cid !== null && item.cid !== undefined) activeRouteCids.add(String(item.cid));
                        if (item.pool_address) activeRouteAddrs.add(item.pool_address.toLowerCase());
                        if (item.fee) {
                            const parts = item.fee.split('|');
                            const feeDisp = parts[0] ? parts[0].trim() : '';
                            const protoName = parts[1] ? parts[1].trim().toLowerCase() : '';
                            if (feeDisp && protoName) activeRouteKeys.add(`${feeDisp}|${protoName}`);
                        }
                    }
                });
            }
        });

        const activePools = pools.filter(p => {
            if (activeRouteCids.size > 0 || activeRouteAddrs.size > 0 || activeRouteKeys.size > 0) {
                const matchCid = p.cid !== null && p.cid !== undefined && activeRouteCids.has(String(p.cid));
                const matchAddr = p.pool_address && activeRouteAddrs.has(p.pool_address.toLowerCase());
                const pKey = `${p.fee_display || ''}|${(p.protocol || '').toLowerCase()}`;
                const matchKey = activeRouteKeys.has(pKey);
                return matchCid || matchAddr || matchKey;
            }
            return (p.tvl || 0) > 1.0;
        });

        // Existing pools with hypothetical post-diversion stats
        activePools.forEach(p => {
            const proto = p.protocol || 'Uniswap V3';
            rowSpecs.push({
                pathTokens: buildPathTokens(p.fee_display, proto, p.hyp_apr_pct),
                cells: {
                    count: p.hyp_count,
                    swaps: p.swaps ?? p.hyp_count,
                    apr: p.hyp_apr_pct,
                    volume: p.hyp_volume,
                    fees: p.hyp_fees,
                    tvl: p.tvl,
                    pct: totalVol > 0 ? (p.hyp_volume / totalVol) * 100 : 0,
                    cid: p.cid,
                    poolId: p.pool_id || p.pool_address,
                    realCount: p.count,
                    realSwaps: p.swaps,
                    realApr: p.apr_pct !== undefined ? p.apr_pct : null,
                    realVolume: p.volume,
                    lastActivity: p.last_activity
                },
                isHypothetical: false,
                network: network,
                protocol: proto
            });
        });

        rowSpecs.forEach(({ pathTokens, cells, isHypothetical, protocol: rowProtocol }) => {
            if (ucProtocol !== 'all') {
                const protoMatch = rowProtocol === ucProtocol || (ucProtocol === 'Uniswap' && rowProtocol.startsWith('Uniswap'));
                if (!protoMatch) return;
            }
            const checkApr = isHypothetical ? cells.apr : (cells.realApr !== null && cells.realApr !== undefined ? cells.realApr : cells.apr);
            const checkFees = isHypothetical ? cells.fees : (cells.realVolume !== undefined ? cells.realVolume : cells.fees);
            const checkCount = isHypothetical ? cells.count : (cells.realCount !== undefined ? cells.realCount : cells.count);

            if (checkApr < minAprVal) return;
            if (checkFees < minMktVal) return;
            if (checkCount < minTxsVal) return;
            appendRow(pathTokens, cells, isHypothetical, rowProtocol);
        });
        updateColumnVisibility();
    };

    const performUndercut = async () => {
        const startToken = startTokenInput.value.trim().toUpperCase();
        const endToken = endTokenInput.value.trim().toUpperCase();
        const startDate = startDateInput.value;
        const endDate = endDateInput.value;

        if (!startToken || !endToken) {
            alert('Please enter both start and end tokens.');
            return;
        }
        if (startToken === '*' || endToken === '*') {
            alert('Undercut backtest requires a specific token pair (no wildcards).');
            return;
        }

        const parseNumInput = (id) => {
            const raw = document.getElementById(id)?.value || '';
            return parseFloat(raw.replace(',', '.'));
        };
        const parseLiquidityInput = (id) => {
            const raw = document.getElementById(id)?.value || '';
            return parseFloat(raw.replace(/[,\s]/g, ''));
        };
        const feeVal = parseNumInput('undercut-fee');
        const liqVal = parseLiquidityInput('undercut-liquidity');
        const rangeVal = parseNumInput('undercut-range');
        if (isNaN(feeVal) || feeVal < 0 || isNaN(liqVal) || liqVal <= 0 || isNaN(rangeVal) || rangeVal <= 0) {
            alert('Please enter valid fee tier, liquidity and range values.');
            return;
        }

        const undercutBtn = document.getElementById('undercut-btn');
        const undercutSection = document.getElementById('undercut-results-section');
        undercutBtn.disabled = true;
        undercutSection.classList.add('hidden');
        noDataMsg.classList.add('hidden');

        try {
            const selectedNetwork = document.getElementById('undercut-network')?.value || '';
            const feeBps = Math.round(feeVal * 100);
            let url = `/api/routes/undercut?start_token=${startToken}&end_token=${endToken}&fee_bps=${feeBps}&liquidity_usd=${liqVal}&range_pct=${rangeVal}`;
            if (startDate) url += `&start_date=${startDate}`;
            if (endDate) url += `&end_date=${endDate}`;
            if (selectedNetwork && selectedNetwork !== 'all') url += `&network=${selectedNetwork}`;

            const response = await fetch(url);
            if (!response.ok) {
                let detail = `API request failed with status ${response.status}`;
                try {
                    const errData = await response.json();
                    if (errData && errData.detail) detail = errData.detail;
                } catch (e) {}
                throw new Error(detail);
            }

            const data = await response.json();
            if (!data.hypothetical || data.pools.length === 0) {
                showError('No swap data found for the specified period and tokens.');
                return;
            }

            renderUndercut(data);
            undercutSection.classList.remove('hidden');
        } catch (error) {
            console.error('Error during undercut backtest:', error);
            showError(error.message || 'Unknown error');
        } finally {
            undercutBtn.disabled = false;
        }
    };

    const undercutBtn = document.getElementById('undercut-btn');
    if (undercutBtn) {
        undercutBtn.addEventListener('click', performUndercut);
    }

    // Allow Enter key to trigger analysis
    [startTokenInput, endTokenInput, startDateInput, endDateInput].forEach(input => {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                performAnalysis();
            }
        });
    });

    // Dropdown UI controls setup
    const dropdownPairs = [
        { btn: document.getElementById('lp-options-btn'), dropdown: document.getElementById('lp-options-dropdown') },
        { btn: document.getElementById('table-columns-btn'), dropdown: document.getElementById('table-columns-dropdown') },
        { btn: document.getElementById('column-selector-btn'), dropdown: document.getElementById('column-selector-dropdown') },
        { btn: document.getElementById('uc-columns-btn'), dropdown: document.getElementById('uc-columns-dropdown') }
    ];

    dropdownPairs.forEach(({ btn, dropdown }) => {
        if (btn && dropdown) {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                dropdownPairs.forEach(p => {
                    if (p.dropdown && p.dropdown !== dropdown) {
                        p.dropdown.classList.add('hidden');
                    }
                });
                dropdown.classList.toggle('hidden');
            });

            dropdown.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.addEventListener('change', () => {
                    updateColumnVisibility();
                });
            });
        }
    });

    // Close dropdowns when clicking outside
    document.addEventListener('click', (e) => {
        dropdownPairs.forEach(({ btn, dropdown }) => {
            if (dropdown && !dropdown.contains(e.target) && btn && !btn.contains(e.target)) {
                dropdown.classList.add('hidden');
            }
        });
    });

    // Input listeners for real-time filtering
    ['min-apr-filter', 'min-mkt-filter', 'min-txs-filter'].forEach(id => {
        const input = document.getElementById(id);
        if (input) {
            input.addEventListener('input', () => {
                filterAndRenderRoutes();
            });
        }
    });

    // Undercut table real-time filters
    ['uc-min-apr-filter', 'uc-min-mkt-filter', 'uc-min-txs-filter'].forEach(id => {
        const input = document.getElementById(id);
        if (input) {
            input.addEventListener('input', () => {
                filterAndRenderUndercut();
            });
        }
    });

    // Comma-format the undercut liquidity input as the user types
    const undercutLiqInput = document.getElementById('undercut-liquidity');
    if (undercutLiqInput) {
        const formatLiquidityInput = (el) => {
            const digits = (el.value || '').replace(/[^\d]/g, '');
            el.value = digits ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : '';
        };
        undercutLiqInput.addEventListener('input', () => {
            const pos = undercutLiqInput.selectionStart;
            const raw = undercutLiqInput.value;
            formatLiquidityInput(undercutLiqInput);
            const delta = undercutLiqInput.value.length - raw.length;
            undercutLiqInput.setSelectionRange(pos + delta, pos + delta);
        });
    }

    const ucProtocolFilter = document.getElementById('uc-protocol-filter');
    if (ucProtocolFilter) {
        ucProtocolFilter.addEventListener('change', () => {
            filterAndRenderUndercut();
        });
    }

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

    const stableShortcutCheckbox = document.getElementById('stable-shortcut-filter');
    if (stableShortcutCheckbox) {
        stableShortcutCheckbox.addEventListener('change', () => {
            filterAndRenderRoutes();
        });
    }

    [startTokenInput, endTokenInput].forEach(input => {
        if (input) {
            input.addEventListener('input', () => {
                updateStableShortcutState();
            });
        }
    });

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
