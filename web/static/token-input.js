// Shared token-input helpers: autocomplete dropdown with logos/families,
// token icon inside the input, and the custom chain selector. Mirrors the
// behavior of the Swaps page (routing.html / app.js) so all pages feel the same.
const TOKEN_IMAGE_MAP = {};
const TOKEN_SLUG_MAP = {};
let ALL_TOKENS_LIST = [];
let ALL_FAMILIES_LIST = [];

// Server-side family expansion cache (keyed by normalized symbol) backed by
// GET /api/coins/search-by-symbol, matching how the backend resolves families
// in /api/routes/analyze.
const FAMILY_SEARCH_CACHE = {};
let familySearchSeq = 0;

const tokenIconUrl = (symbol) => {
    const s = String(symbol || '').toLowerCase();
    if (!s) return '';
    return `https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@1a63530be6e374711a8554f31b17e4cb92c25fa5/128/color/${s}.png`;
};

// Normalize the JSON:API compound document from /api/coins/search-by-symbol
// back into the flat [{symbol, name, contracts:[{chain}]}] array the
// dropdown rendering expects.
const normalizeCoinSearch = (data) => {
    if (!data || !data.data) return [];
    const includedById = {};
    (data.included || []).forEach(r => { includedById[r.type + ':' + r.id] = r; });
    const coins = Array.isArray(data.data) ? data.data : [data.data];
    return coins.map(c => {
        const attrs = c.attributes || {};
        const contractRefs = (c.relationships && c.relationships.contracts && c.relationships.contracts.data) || [];
        const contracts = contractRefs.map(ref => {
            const cc = includedById[ref.type + ':' + ref.id];
            return cc && cc.attributes ? { chain: cc.attributes.chain } : null;
        }).filter(Boolean);
        return { symbol: attrs.symbol, name: attrs.name, contracts };
    });
};

// Normalize the /api/coin-families compound document into the legacy
// {families: {FAMILY:[symbols]}, symbol_family_map: {SYMBOL:FAMILY}} shape.
const normalizeFamilyMap = (data) => {
    const fams = data && data.data ? (Array.isArray(data.data) ? data.data : [data.data]) : [];
    const includedById = {};
    (data.included || []).forEach(r => { includedById[r.type + ':' + r.id] = r; });
    const families = {};
    const symbolFamilyMap = {};
    fams.forEach(f => {
        const famName = (f.attributes && f.attributes.name) || f.id;
        const memberRefs = (f.relationships && f.relationships.members && f.relationships.members.data) || [];
        const symbols = memberRefs.map(ref => {
            const coin = includedById[ref.type + ':' + ref.id];
            return coin && coin.attributes ? coin.attributes.symbol : null;
        }).filter(Boolean);
        families[famName] = symbols;
        symbols.forEach(s => { if (!(s in symbolFamilyMap)) symbolFamilyMap[s] = famName; });
    });
    return { families, symbol_family_map: symbolFamilyMap };
};

const getPrincipalSymbol = (symbol) => {
    if (!symbol) return '';
    let u = String(symbol).toUpperCase();

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

// Fetch token metadata + families for lookahead autocomplete.
const loadTokenMetadata = () => {
    fetch('/api/coins/list')
        .then(response => response.json())
        .then(coins => {
            ALL_TOKENS_LIST = coins;
            coins.forEach(coin => {
                if (coin.symbol) {
                    const upperSymbol = coin.symbol.toUpperCase();
                    if (coin.image) TOKEN_IMAGE_MAP[upperSymbol] = coin.image;
                    if (coin.slug) TOKEN_SLUG_MAP[upperSymbol] = coin.slug;
                }
            });
        })
        .catch(error => console.error('Error fetching token images:', error));

    fetch('/api/coin-families')
        .then(res => res.json())
        .then(data => {
            const fams = normalizeFamilyMap(data).families || {};
            ALL_FAMILIES_LIST = Object.keys(fams).map(f => ({
                name: f,
                membersCount: fams[f]?.length || 0
            }));
        })
        .catch(err => console.error('Error fetching coin families:', err));
};

// Debounced server-side family lookup. GET /api/coins/search-by-symbol returns
// every coin in the queried symbol's family plus its contracts; results are
// cached per symbol so the dropdown renders instantly on subsequent keystrokes.
const fetchFamilyExpansion = (symbol) => {
    const key = (symbol || '').trim().toUpperCase();
    if (!key || key === '*' || key in FAMILY_SEARCH_CACHE) return;
    FAMILY_SEARCH_CACHE[key] = null;
    const seq = ++familySearchSeq;
    setTimeout(() => {
        fetch(`/api/coins/search-by-symbol?symbol=${encodeURIComponent(key)}&include_coin_families=true`)
            .then(res => res.ok ? res.json() : null)
            .then(data => {
                if (seq !== familySearchSeq) return;
                FAMILY_SEARCH_CACHE[key] = normalizeCoinSearch(data);
                document.querySelectorAll('.token-input-field, #token-input').forEach(el => {
                    if (el.dataset.autocompleteInitialized && el.value.trim().toUpperCase() === key) {
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                });
            })
            .catch(() => { if (seq === familySearchSeq) FAMILY_SEARCH_CACHE[key] = []; });
    }, 250);
};

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
        const iconSrc = TOKEN_IMAGE_MAP[sym] || TOKEN_IMAGE_MAP[principal] || tokenIconUrl(principal) || tokenIconUrl(sym);
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

    // When the user picks an item the popup must close and not re-open on the
    // synthetic input event dispatched below.
    let justSelected = false;

    const renderDropdown = (query) => {
        updateInputIcon();
        const q = (query || '').trim().toUpperCase();
        dropdown.innerHTML = '';
        selectedIndex = -1;

        // Kick off a server-side family expansion for the current query;
        // when the response lands the dropdown re-renders with the expanded
        // members (plus their contracts).
        if (q !== '' && q !== '*' && !(q in FAMILY_SEARCH_CACHE)) {
            fetchFamilyExpansion(q);
        }

        let matches = [];

        if (q === '' || q === '*') {
            matches.push({ type: 'wildcard', symbol: '*', name: 'Any Token (Wildcard)', icon: '/static/favicon.png' });
        }

        // Server-side family expansion: GET /api/coins/search-by-symbol returns
        // every coin in the queried symbol's family plus its contracts. The
        // request only succeeds for an exact symbol/family name, so partial
        // queries keep the client-side family-name entry as a fallback.
        const cachedFamily = FAMILY_SEARCH_CACHE[q];
        if (q !== '' && q !== '*') {
            const expanded = cachedFamily || null;
            if (expanded && expanded.length > 0) {
                expanded.forEach(coin => {
                    const sym = (coin.symbol || '').toUpperCase();
                    const chains = (coin.contracts || []).map(c => c.chain).filter(Boolean);
                    matches.push({
                        type: 'family',
                        symbol: sym,
                        name: chains.length ? `${coin.name || sym} · ${chains.join(', ')}` : (coin.name || sym),
                        icon: getPrincipalSymbol(sym.replace('_YBA', ''))
                    });
                });
            } else {
                ALL_FAMILIES_LIST.forEach(fam => {
                    if (fam.name.includes(q)) {
                        matches.push({
                            type: 'family',
                            symbol: fam.name,
                            name: `Family (${fam.membersCount} coins)`,
                            icon: getPrincipalSymbol(fam.name.replace('_YBA', ''))
                        });
                    }
                });
            }
        } else {
            ALL_FAMILIES_LIST.forEach(fam => {
                matches.push({
                    type: 'family',
                    symbol: fam.name,
                    name: `Family (${fam.membersCount} coins)`,
                    icon: getPrincipalSymbol(fam.name.replace('_YBA', ''))
                });
            });
        }

        let coinMatches = [];
        ALL_TOKENS_LIST.forEach(coin => {
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

        matches.forEach((item) => {
            const groupName = item.type === 'wildcard' ? 'Wildcard' : (item.type === 'family' ? 'Coin Families' : 'Tokens');
            if (groupName !== currentGroup) {
                currentGroup = groupName;
                html += `<div class="token-autocomplete-group-title">${currentGroup}</div>`;
            }

            let iconSrc = item.icon;
            if (!iconSrc || item.type === 'coin' || item.type === 'family') {
                const principal = getPrincipalSymbol(item.symbol);
                iconSrc = TOKEN_IMAGE_MAP[item.symbol] || TOKEN_IMAGE_MAP[principal] || tokenIconUrl(principal) || tokenIconUrl(item.symbol);
            }

            html += `
                <div class="token-autocomplete-item" data-symbol="${item.symbol}">
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
        justSelected = true;
        inputEl.dispatchEvent(new Event('change', { bubbles: true }));
        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
    };

    // Track whether the input was clicked while the dropdown was already open.
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
        if (justSelected) { justSelected = false; return; }
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

    // Close when focus leaves the input entirely (Tab to another field, click
    // a non-DOM target, etc.) — without this the popup stays open after blur.
    inputEl.addEventListener('focusout', () => {
        if (!container.contains(document.activeElement)) {
            dropdown.classList.remove('active');
        }
    });

    setTimeout(updateInputIcon, 200);
    setTimeout(updateInputIcon, 1000);
};

const CHAIN_ICONS = {
    'all': "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='%23ff007a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolygon points='12 2 2 7 12 12 22 7 12 2'%3E%3C/polygon%3E%3Cpolyline points='2 17 12 22 22 17'%3E%3C/polyline%3E%3Cpolyline points='2 12 12 17 22 12'%3E%3C/polyline%3E%3C/svg%3E",
    'Ethereum': 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/info/logo.png',
    'Arbitrum': 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/arbitrum/info/logo.png',
    'Base': 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/base/info/logo.png',
    'BNB': 'https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/binance/info/logo.png'
};

const initCustomChainSelector = (selectEl) => {
    if (!selectEl || selectEl.dataset.customSelectorInitialized) return;
    selectEl.dataset.customSelectorInitialized = 'true';

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
        const iconSrc = CHAIN_ICONS[val] || CHAIN_ICONS['all'];

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
            const iconSrc = CHAIN_ICONS[val] || CHAIN_ICONS['all'];
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
