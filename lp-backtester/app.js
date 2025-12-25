// Global State
let baseSearchInput, quoteSearchInput, baseResults, quoteResults, swapBtn;
let aprInput, startDateInput, endDateInput, addStrategyBtn, runBtn;
let allCoins = [];
let baseAsset = { id: 'ethereum', symbol: 'eth', name: 'Ethereum' };
let quoteAsset = { id: 'usd-coin', symbol: 'usdc', name: 'USDC' };

let strategies = [];
let nextStrategyId = 1;
let summaryChartInstance = null;
let enlargedChartInstance = null;
let lastCalculatedResults = []; // Store results for enlargement later

// --- URL Parameter Management ---

function getURLParams() {
    const params = new URLSearchParams(window.location.search);
    const result = {
        token1: params.get('token1'),
        token2: params.get('token2'),
        apr: params.get('apr'),
        start: params.get('start'),
        end: params.get('end'),
        strategies: []
    };

    // Parse strategy[n].key parameters
    const strategyMap = {};
    for (const [key, value] of params.entries()) {
        const match = key.match(/^strategy\[(\d+)\]\.(.+)$/);
        if (match) {
            const index = match[1];
            const property = match[2];
            if (!strategyMap[index]) strategyMap[index] = {};

            const parts = property.split('.');
            let current = strategyMap[index];
            for (let i = 0; i < parts.length; i++) {
                const part = parts[i];
                if (i === parts.length - 1) {
                    // Final part: set the value
                    if (current[part] && typeof current[part] === 'object') {
                        current[part]._ = value;
                    } else {
                        current[part] = value;
                    }
                } else {
                    // Intermediate part: ensure object exists
                    if (!current[part] || typeof current[part] !== 'object') {
                        const oldVal = current[part];
                        current[part] = oldVal ? { _: oldVal } : {};
                    }
                    current = current[part];
                }
            }
        }
    }

    // Convert map to sorted array
    const sortedIndices = Object.keys(strategyMap).sort((a, b) => a - b);
    result.strategies = sortedIndices.map(idx => {
        const s = strategyMap[idx];
        const isReb = s.rebalance === 'true' || s.reb === 'true' || (typeof s.rebalance === 'object' && s.rebalance._ === 'true');

        // Priority: type > rebalance.type > (rebalance:boolean ? 'time-delayed' : 'simple')
        let type = s.type || s.rebalance?.type || (isReb ? 'time-delayed' : 'simple');

        // Backward compatibility mapping
        if (type === 'none') type = 'simple';
        if (type === 'delayed') type = 'time-delayed';

        return {
            name: s.name,
            min: s.range?.min || s.min,
            max: s.range?.max || s.max,
            type: type,
            rebMin: s.rebalance?.range?.min || s.rebalanceRange?.min || s.rebMin,
            rebMax: s.rebalance?.range?.max || s.rebalanceRange?.max || s.rebMax,
            rebDelay: s.rebalance?.delay || s.rebalanceDelay || s.rebDelay,
            rebMan: s.rebalance?.manual === 'true' || s.rebalanceManual === 'true' || s.rebMan === 'true'
        };
    });

    return result;
}

function updateURLParams() {
    const params = new URLSearchParams(window.location.search);

    // Clear old strategy params
    for (const key of Array.from(params.keys())) {
        if (key.startsWith('strategy[')) params.delete(key);
    }

    if (baseAsset) params.set('token1', baseAsset.symbol.toLowerCase());
    if (quoteAsset) params.set('token2', quoteAsset.symbol.toLowerCase());
    if (aprInput) params.set('apr', aprInput.value);
    if (startDateInput) params.set('start', startDateInput.value);
    if (endDateInput) params.set('end', endDateInput.value);

    strategies.forEach((s, i) => {
        const idx = i + 1; // 1-based indexing for readability
        const prefix = `strategy[${idx}]`;
        params.set(`${prefix}.name`, s.block.querySelector('.strategy-title-input').value);
        params.set(`${prefix}.range.min`, s.minRangeInput.value);
        params.set(`${prefix}.range.max`, s.maxRangeInput.value);

        const type = s.rebalanceType.value;
        params.set(`${prefix}.type`, type);

        if (type !== 'simple') {
            params.set(`${prefix}.rebalance.range.min`, s.rebalanceMinInput.value);
            params.set(`${prefix}.rebalance.range.max`, s.rebalanceMaxInput.value);
            params.set(`${prefix}.rebalance.delay`, s.rebalanceDelayInput.value);
            params.set(`${prefix}.rebalance.manual`, s.rebalanceRangeManuallyChanged);
        }
    });

    const newUrl = window.location.pathname + '?' + params.toString();
    window.history.replaceState({}, '', newUrl);
}

// --- Initialization ---

async function init() {
    // Global Elements
    baseSearchInput = document.getElementById('base-search');
    quoteSearchInput = document.getElementById('quote-search');
    baseResults = document.getElementById('base-results');
    quoteResults = document.getElementById('quote-results');
    swapBtn = document.getElementById('swap-btn');
    aprInput = document.getElementById('apr-input');
    startDateInput = document.getElementById('start-date');
    endDateInput = document.getElementById('end-date');
    addStrategyBtn = document.getElementById('add-strategy-btn');
    runBtn = document.getElementById('run-btn');

    // Attach Global Listeners
    if (baseSearchInput && baseResults) setupSearch(baseSearchInput, baseResults, (coin) => {
        baseAsset = coin;
        updateURLParams();
    });
    if (quoteSearchInput && quoteResults) setupSearch(quoteSearchInput, quoteResults, (coin) => {
        quoteAsset = coin;
        updateURLParams();
    });

    if (swapBtn) {
        swapBtn.addEventListener('click', () => {
            const temp = baseAsset;
            baseAsset = quoteAsset;
            quoteAsset = temp;
            if (baseSearchInput) baseSearchInput.value = baseAsset.symbol.toUpperCase();
            if (quoteSearchInput) quoteSearchInput.value = quoteAsset.symbol.toUpperCase();
            updateURLParams();
        });
    }

    if (addStrategyBtn) {
        addStrategyBtn.addEventListener('click', () => addStrategy());
    }

    const strategiesContainer = document.getElementById('strategies-container');
    if (strategiesContainer) {
        strategiesContainer.addEventListener('click', (e) => {
            const toggleBtn = e.target.closest('.toggle-strategy-btn');
            const removeBtn = e.target.closest('.remove-strategy-btn');
            const enlargeBtn = e.target.closest('.enlarge-btn');

            if (toggleBtn) {
                const block = toggleBtn.closest('.strategy-block');
                if (block) block.classList.toggle('collapsed');
            } else if (removeBtn) {
                const block = removeBtn.closest('.strategy-block');
                if (block && block.dataset.id) {
                    removeStrategy(parseInt(block.dataset.id));
                }
            } else if (enlargeBtn) {
                handleEnlarge(enlargeBtn);
            }
        });
    }

    const summarySection = document.getElementById('summary-section');
    if (summarySection) {
        summarySection.addEventListener('click', (e) => {
            const enlargeBtn = e.target.closest('.enlarge-btn');
            if (enlargeBtn) handleEnlarge(enlargeBtn);
        });
    }

    const closeModalBtn = document.getElementById('close-modal-btn');
    const modalOverlay = document.getElementById('modal-overlay');
    if (closeModalBtn && modalOverlay) {
        closeModalBtn.addEventListener('click', hideModal);
        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) hideModal();
        });
    }

    if (runBtn) {
        runBtn.addEventListener('click', updateAllCharts);
    }

    // Global settings listeners
    if (aprInput) aprInput.addEventListener('input', updateURLParams);
    if (startDateInput) startDateInput.addEventListener('input', updateURLParams);
    if (endDateInput) endDateInput.addEventListener('input', updateURLParams);

    // Set Defaults
    if (baseSearchInput) baseSearchInput.value = baseAsset.symbol.toUpperCase();
    if (quoteSearchInput) quoteSearchInput.value = quoteAsset.symbol.toUpperCase();

    if (startDateInput) {
        const d = new Date();
        d.setFullYear(d.getFullYear() - 2);
        startDateInput.valueAsDate = d;
    }
    if (endDateInput) {
        endDateInput.valueAsDate = new Date();
    }

    // Load coins then add initial strategy
    await fetchCoinList();

    // Check URL Params
    const urlParams = getURLParams();

    if (urlParams.apr) aprInput.value = urlParams.apr;
    if (urlParams.start) startDateInput.value = urlParams.start;
    if (urlParams.end) endDateInput.value = urlParams.end;

    if (urlParams.token1) {
        const coin1 = allCoins.find(c => c.symbol.toLowerCase() === urlParams.token1.toLowerCase());
        if (coin1) {
            baseAsset = coin1;
            if (baseSearchInput) baseSearchInput.value = baseAsset.symbol.toUpperCase();
        }
    }
    if (urlParams.token2) {
        const coin2 = allCoins.find(c => c.symbol.toLowerCase() === urlParams.token2.toLowerCase());
        if (coin2) {
            quoteAsset = coin2;
            if (quoteSearchInput) quoteSearchInput.value = quoteAsset.symbol.toUpperCase();
        }
    }

    if (urlParams.strategies && urlParams.strategies.length > 0) {
        urlParams.strategies.forEach(sConfig => {
            addStrategy(sConfig);
        });
    } else {
        addStrategy({ name: "Wide Ranged Non-rebalancing" });
    }

    updateAllCharts(); // Initial run
}

// --- Strategy Management ---

function addStrategy(config = {}) {
    const container = document.getElementById('strategies-container');
    const template = document.getElementById('strategy-template');
    if (!container || !template) return;

    const clone = template.content.cloneNode(true);
    const block = clone.querySelector('.strategy-block');
    const id = nextStrategyId++;
    block.dataset.id = id;

    const titleInput = block.querySelector('.strategy-title-input');
    titleInput.value = config.name || `Strategy #${id}`;
    block.querySelector('.strategy-id').textContent = `#${id}`;

    // Elements inside the block
    const strategy = {
        id,
        block,
        minRangeInput: block.querySelector('.min-range'),
        maxRangeInput: block.querySelector('.max-range'),
        rebalanceType: block.querySelector('.rebalance-type'),
        rebalanceParamsRow: block.querySelector('.rebalance-params-row'),
        rebalanceMinInput: block.querySelector('.rebalance-min'),
        rebalanceMaxInput: block.querySelector('.rebalance-max'),
        rebalanceDelayInput: block.querySelector('.rebalance-delay'),
        loading: block.querySelector('.loading-overlay'),
        errorMessage: block.querySelector('.error-message'),
        canvas: block.querySelector('.strategy-chart'),
        chartInstance: null,
        relativeCanvas: block.querySelector('.relative-chart'),
        relativeChartInstance: null,
        rebalanceRangeManuallyChanged: config.rebMan || false
    };

    // Apply config if provided
    if (config.min !== undefined) strategy.minRangeInput.value = config.min;
    if (config.max !== undefined) strategy.maxRangeInput.value = config.max;
    if (config.type !== undefined) strategy.rebalanceType.value = config.type;
    if (config.rebMin !== undefined) strategy.rebalanceMinInput.value = config.rebMin;
    if (config.rebMax !== undefined) strategy.rebalanceMaxInput.value = config.rebMax;
    if (config.rebDelay !== undefined) strategy.rebalanceDelayInput.value = config.rebDelay;

    // Visibility toggle based on type
    const toggleRebalanceFields = () => {
        const isNone = strategy.rebalanceType.value === 'simple';
        if (isNone) {
            strategy.rebalanceParamsRow.classList.add('disabled');
            strategy.rebalanceMinInput.disabled = true;
            strategy.rebalanceMaxInput.disabled = true;
            strategy.rebalanceDelayInput.disabled = true;
        } else {
            strategy.rebalanceParamsRow.classList.remove('disabled');
            strategy.rebalanceMinInput.disabled = false;
            strategy.rebalanceMaxInput.disabled = false;
            strategy.rebalanceDelayInput.disabled = false;
        }
    };
    toggleRebalanceFields();

    // Event Listeners for serialization and URL updates
    const inputsToWatch = [
        strategy.minRangeInput, strategy.maxRangeInput,
        strategy.rebalanceType, strategy.rebalanceMinInput,
        strategy.rebalanceMaxInput, strategy.rebalanceDelayInput,
        titleInput
    ];
    inputsToWatch.forEach(input => {
        input.addEventListener('input', updateURLParams);
        input.addEventListener('change', updateURLParams);
    });

    strategy.rebalanceType.addEventListener('change', () => {
        toggleRebalanceFields();
        checkGlobalValidity();
    });

    // Sync logic: LP Range -> Rebalance Range (if not decoupled)
    const syncRebalanceRange = () => {
        if (!strategy.rebalanceRangeManuallyChanged) {
            strategy.rebalanceMinInput.value = strategy.minRangeInput.value;
            strategy.rebalanceMaxInput.value = strategy.maxRangeInput.value;
        }
    };

    // Combined Range Listeners
    strategy.minRangeInput.addEventListener('input', () => {
        syncRebalanceRange();
        checkGlobalValidity();
    });
    strategy.maxRangeInput.addEventListener('input', () => {
        syncRebalanceRange();
        checkGlobalValidity();
    });

    strategy.rebalanceMinInput.addEventListener('input', () => {
        strategy.rebalanceRangeManuallyChanged = true;
        checkGlobalValidity();
    });
    strategy.rebalanceMaxInput.addEventListener('input', () => {
        strategy.rebalanceRangeManuallyChanged = true;
        checkGlobalValidity();
    });

    strategy.rebalanceDelayInput.addEventListener('input', checkGlobalValidity);

    // Initial check
    checkGlobalValidity();

    strategies.push(strategy);
    container.appendChild(clone);
}

// Range Validation Logic
function validateRange(minEl, maxEl) {
    let minVal = parseFloat(minEl.value);
    let maxVal = parseFloat(maxEl.value);

    if (isNaN(minVal) || isNaN(maxVal)) {
        minEl.classList.remove('invalid-input');
        maxEl.classList.remove('invalid-input');
        return true;
    }

    const isValid = minVal <= maxVal;
    if (isValid) {
        minEl.classList.remove('invalid-input');
        maxEl.classList.remove('invalid-input');
    } else {
        minEl.classList.add('invalid-input');
        maxEl.classList.add('invalid-input');
    }
    return isValid;
}

function checkGlobalValidity() {
    const runBtn = document.getElementById('run-btn');
    if (!runBtn) return;

    let allValid = true;

    strategies.forEach(s => {
        const isLpValid = validateRange(s.minRangeInput, s.maxRangeInput);
        let isRebValid = true;
        if (s.rebalanceType.value !== 'simple') {
            isRebValid = validateRange(s.rebalanceMinInput, s.rebalanceMaxInput);

            // Settled rebalance requires delay > 1
            if (s.rebalanceType.value === 'settled') {
                const delay = parseInt(s.rebalanceDelayInput.value);
                if (isNaN(delay) || delay <= 1) {
                    isRebValid = false;
                    s.rebalanceDelayInput.classList.add('invalid-input');
                } else {
                    s.rebalanceDelayInput.classList.remove('invalid-input');
                }
            } else {
                s.rebalanceDelayInput.classList.remove('invalid-input');
            }
        } else {
            // ... (rest of the logic)
            s.rebalanceMinInput.classList.remove('invalid-input');
            s.rebalanceMaxInput.classList.remove('invalid-input');
            s.rebalanceDelayInput.classList.remove('invalid-input');
        }

        if (!isLpValid || !isRebValid) {
            allValid = false;
        }
    });

    runBtn.disabled = !allValid;
}

function removeStrategy(id) {
    const index = strategies.findIndex(s => s.id === id);
    if (index === -1) return;

    const strategy = strategies[index];
    if (strategy.chartInstance) strategy.chartInstance.destroy();
    if (strategy.relativeChartInstance) strategy.relativeChartInstance.destroy();
    strategy.block.remove();
    strategies.splice(index, 1);
    updateURLParams();
}

// --- Search Logic ---

async function fetchCoinList() {
    try {
        const cached = localStorage.getItem('coingecko_top_100');
        const timestamp = localStorage.getItem('coingecko_top_100_ts');
        const now = Date.now();

        // Load from cache if valid
        if (cached && timestamp && (now - timestamp < 86400000)) {
            allCoins = JSON.parse(cached);
        } else {
            const response = await fetch('https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1&sparkline=false');
            if (!response.ok) throw new Error('Failed to fetch coin list');
            allCoins = await response.json();

            localStorage.setItem('coingecko_top_100', JSON.stringify(allCoins));
            localStorage.setItem('coingecko_top_100_ts', now);
        }

        // Always check for EURC to be extra sure (handles potential cache mismatches)
        if (!allCoins.find(c => c.id === 'euro-coin' || c.symbol === 'eurc')) {
            const eurcResponse = await fetch('https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=euro-coin');
            if (eurcResponse.ok) {
                const eurcData = await eurcResponse.json();
                if (eurcData.length > 0) {
                    allCoins.push(eurcData[0]);
                    // Update cache with EURC if we just added it
                    localStorage.setItem('coingecko_top_100', JSON.stringify(allCoins));
                }
            }
        }
    } catch (error) {
        console.error("Error loading coins:", error);
    }
}

function setupSearch(input, resultsContainer, setAssetCallback) {
    if (!input || !resultsContainer) return;
    input.addEventListener('input', () => {
        const query = input.value.toLowerCase();
        if (query.length < 2) {
            resultsContainer.classList.add('hidden');
            return;
        }
        const matches = allCoins.filter(c =>
            c.symbol.toLowerCase().startsWith(query) ||
            c.name.toLowerCase().includes(query)
        ).slice(0, 50);

        renderResults(matches, resultsContainer, (coin) => {
            input.value = coin.symbol.toUpperCase();
            resultsContainer.classList.add('hidden');
            setAssetCallback(coin);
        });
    });

    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !resultsContainer.contains(e.target)) {
            resultsContainer.classList.add('hidden');
        }
    });

    input.addEventListener('focus', () => {
        if (input.value.length >= 2) input.dispatchEvent(new Event('input'));
    });
}

function renderResults(coins, container, onSelect) {
    container.innerHTML = '';
    if (coins.length === 0) {
        container.classList.add('hidden');
        return;
    }
    coins.forEach(coin => {
        const div = document.createElement('div');
        div.className = 'result-item';
        div.innerHTML = `<span class="symbol">${coin.symbol}</span><span class="name">${coin.name}</span>`;
        div.addEventListener('click', () => onSelect(coin));
        container.appendChild(div);
    });
    container.classList.remove('hidden');
}

// --- Backtest Calculations ---

async function updateAllCharts() {
    if (!baseAsset || !quoteAsset) return;

    // Show all loading overlays
    strategies.forEach(s => {
        if (s.loading) s.loading.classList.add('active');
        if (s.errorMessage) {
            s.errorMessage.classList.add('hidden');
            s.errorMessage.textContent = '';
        }
    });

    try {
        const apiKey = CONFIG.CRYPTOCOMPARE_API_KEY;
        const startDatePoints = new Date(startDateInput.value).getTime();
        let endDatePoints = Date.now();
        if (endDateInput && endDateInput.value) {
            const d = new Date(endDateInput.value);
            d.setHours(23, 59, 59, 999);
            endDatePoints = d.getTime();
        }

        // Fetch shared price data once
        const [baseData, quoteData] = await Promise.all([
            fetchHistory(baseAsset.symbol.toUpperCase(), apiKey),
            fetchHistory(quoteAsset.symbol.toUpperCase(), apiKey)
        ]);

        let ratioSeries = calculateRatioSeries(baseData, quoteData);
        ratioSeries = ratioSeries.filter(p => p[0] >= startDatePoints && p[0] <= endDatePoints);

        if (ratioSeries.length === 0) throw new Error("No data for selected date range");

        // Global shared params
        const baseAprPct = parseFloat(aprInput.value) / 100;
        const allResults = [];

        // Process each strategy
        strategies.forEach(s => {
            try {
                const minPct = parseFloat(s.minRangeInput.value) / 100;
                const maxPct = parseFloat(s.maxRangeInput.value) / 100;
                const rebMinPct = parseFloat(s.rebalanceMinInput.value) / 100;
                const rebMaxPct = parseFloat(s.rebalanceMaxInput.value) / 100;
                const rebalanceMode = s.rebalanceType.value;
                const delayDays = parseInt(s.rebalanceDelayInput.value) || 1;
                const strategyName = s.block.querySelector('.strategy-title-input').value || `Strategy #${s.id}`;

                const results = calculateV3Backtest(ratioSeries, minPct, maxPct, rebMinPct, rebMaxPct, baseAprPct, rebalanceMode, delayDays);

                s.chartInstance = renderChart(results, s.canvas, s.chartInstance);
                s.relativeChartInstance = renderRelativeChart(results, s.relativeCanvas, s.relativeChartInstance);
                s.lastResults = results; // Store for enlargement

                allResults.push({ name: strategyName, ...results });
            } catch (err) {
                console.error(`Strategy ${s.id} error:`, err);
                if (s.errorMessage) {
                    s.errorMessage.textContent = err.message;
                    s.errorMessage.classList.remove('hidden');
                }
            } finally {
                if (s.loading) s.loading.classList.remove('active');
            }
        });

        // Render Summary Chart
        const summarySection = document.getElementById('summary-section');
        if (summarySection) {
            if (allResults.length > 0) {
                summarySection.classList.remove('hidden');
                summaryChartInstance = renderSummaryChart(allResults, document.getElementById('summaryChart'), summaryChartInstance);
                lastCalculatedResults = allResults;
            } else {
                summarySection.classList.add('hidden');
                lastCalculatedResults = [];
            }
        }

    } catch (globalError) {
        console.error("Global Error:", globalError);
        strategies.forEach(s => {
            if (s.errorMessage) {
                s.errorMessage.textContent = globalError.message;
                s.errorMessage.classList.remove('hidden');
            }
            if (s.loading) s.loading.classList.remove('active');
        });
    }
}

async function fetchHistory(symbol, apiKey) {
    const url = `https://min-api.cryptocompare.com/data/v2/histoday?fsym=${symbol}&tsym=USD&allData=true&api_key=${apiKey}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`API Error ${res.status}`);
    const json = await res.json();
    if (json.Response === 'Error') throw new Error(json.Message);
    return json.Data.Data.map(d => [d.time * 1000, d.close]);
}

function calculateRatioSeries(basePrices, quotePrices) {
    const series = [];
    let qIdx = 0;
    for (let i = 0; i < basePrices.length; i++) {
        const [time, bPrice] = basePrices[i];
        while (qIdx < quotePrices.length - 1 &&
            Math.abs(quotePrices[qIdx + 1][0] - time) < Math.abs(quotePrices[qIdx][0] - time)) {
            qIdx++;
        }
        const [qTime, qPrice] = quotePrices[qIdx];
        if (qPrice !== 0) series.push([time, bPrice / qPrice]);
    }
    return series;
}

function getLikidityAndAmounts(P, P_min, P_max, V_target) {
    const sqrtP = Math.sqrt(P);
    const sqrtPa = Math.sqrt(P_min);
    const sqrtPb = Math.sqrt(P_max);
    let x_L1 = 0, y_L1 = 0;

    if (P < P_min) {
        x_L1 = (1 / sqrtPa - 1 / sqrtPb);
    } else if (P > P_max) {
        y_L1 = (sqrtPb - sqrtPa);
    } else {
        x_L1 = (1 / sqrtP - 1 / sqrtPb);
        y_L1 = (sqrtP - sqrtPa);
    }

    const V_L1 = x_L1 * P + y_L1;
    const L = V_target / V_L1;
    return { L, x: x_L1 * L, y: y_L1 * L };
}

function calculateV3Backtest(priceSeries, minPct, maxPct, rebMinPct, rebMaxPct, baseApr, rebalanceMode, delayDays) {
    const P0_initial = priceSeries[0][1];
    let P0 = P0_initial;
    let P_min = P0 * (1 + minPct);
    let P_max = P0 * (1 + maxPct);
    let P_reb_min = P0 * (1 + rebMinPct);
    let P_reb_max = P0 * (1 + rebMaxPct);

    let currentCapital = 100;
    let { L } = getLikidityAndAmounts(P0, P_min, P_max, currentCapital);

    const initialPos = getLikidityAndAmounts(P0, P_min, P_max, 100);
    const initial_x_hodl = initialPos.x, initial_y_hodl = initialPos.y;
    const amt_asset1 = 100 / P0_initial, amt_asset2 = 100;

    const hodlData = [], lpTotalData = [], asset1Data = [], asset2Data = [];
    const minRangeSeries = [], maxRangeSeries = [];
    const minRebSeries = [], maxRebSeries = [];
    let accumulatedFees = 0, daysOutOfRange = 0;

    const rangeWidthBase = 1.5; // for -50% to +100%

    for (let i = 0; i < priceSeries.length; i++) {
        const [time, P] = priceSeries[i];
        hodlData.push([time, initial_x_hodl * P + initial_y_hodl]);
        asset1Data.push([time, amt_asset1 * P]);
        asset2Data.push([time, amt_asset2]);

        const sqrtP = Math.sqrt(P), sqrtPa = Math.sqrt(P_min), sqrtPb = Math.sqrt(P_max);
        let x_t = 0, y_t = 0, inRange = false;

        if (P < P_min) {
            x_t = (1 / sqrtPa - 1 / sqrtPb) * L;
        } else if (P > P_max) {
            y_t = (sqrtPb - sqrtPa) * L;
        } else {
            x_t = (1 / sqrtP - 1 / sqrtPb) * L;
            y_t = (sqrtP - sqrtPa) * L;
            inRange = true;
        }

        let val_lp_principal = x_t * P + y_t;

        if (i > 0) {
            const yearsElapsed = (time - priceSeries[i - 1][0]) / (1000 * 60 * 60 * 24 * 365);
            if (inRange) {
                const effectiveApr = baseApr * (rangeWidthBase / (maxPct - minPct));
                accumulatedFees += val_lp_principal * effectiveApr * yearsElapsed;
            }
        }

        // Rebalance logic
        let shouldRebalance = false;
        let rebalanceCenterPrice = P;

        if (rebalanceMode !== 'simple') {
            if (P < P_reb_min || P > P_reb_max) {
                daysOutOfRange++;
            } else {
                daysOutOfRange = 0;
            }

            if (rebalanceMode === 'time-delayed' && daysOutOfRange >= delayDays) {
                shouldRebalance = true;
            } else if (rebalanceMode === 'settled' && daysOutOfRange >= delayDays) {
                // Check stability in the last delayDays
                const window = priceSeries.slice(i - delayDays + 1, i + 1);
                const prices = window.map(p => p[1]);

                // Geometric Average
                const sumLog = prices.reduce((a, b) => a + Math.log(b), 0);
                const geoAvg = Math.exp(sumLog / delayDays);

                // Stability Check: prices within user-defined rebalance boundaries relative to geoAvg
                const isStable = prices.every(p => {
                    const rel = p / geoAvg;
                    return rel >= (1 + rebMinPct) && rel <= (1 + rebMaxPct);
                });

                if (isStable) {
                    shouldRebalance = true;
                    rebalanceCenterPrice = geoAvg;
                }
            }
        }

        if (shouldRebalance) {
            currentCapital = val_lp_principal + accumulatedFees;
            accumulatedFees = 0;
            P0 = rebalanceCenterPrice;
            P_min = P0 * (1 + minPct);
            P_max = P0 * (1 + maxPct);
            P_reb_min = P0 * (1 + rebMinPct);
            P_reb_max = P0 * (1 + rebMaxPct);
            L = getLikidityAndAmounts(P0, P_min, P_max, currentCapital).L;
            val_lp_principal = currentCapital;
            daysOutOfRange = 0;
        }

        lpTotalData.push([time, val_lp_principal + accumulatedFees]);
        minRangeSeries.push((P_min / P0_initial) * 100);
        maxRangeSeries.push((P_max / P0_initial) * 100);
        minRebSeries.push((P_reb_min / P0_initial) * 100);
        maxRebSeries.push((P_reb_max / P0_initial) * 100);
    }

    const relativeSeries = lpTotalData.map((d, i) => {
        const hodlVal = hodlData[i][1];
        return [d[0], ((d[1] / hodlVal) - 1) * 100];
    });

    return { hodlData, lpTotalData, asset1Data, asset2Data, accumulatedFees, minRangeSeries, maxRangeSeries, minRebSeries, maxRebSeries, relativeSeries };
}

function renderChart(results, canvas, existingInstance) {
    if (existingInstance) existingInstance.destroy();

    const { hodlData, lpTotalData, asset1Data, asset2Data, minRangeSeries, maxRangeSeries, minRebSeries, maxRebSeries } = results;
    const labels = hodlData.map(d => new Date(d[0]).toLocaleDateString());

    return new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Rebalance Max', data: maxRebSeries, borderColor: 'rgba(16, 185, 129, 0.4)', borderWidth: 1, pointRadius: 0, borderDash: [2, 2], fill: false },
                { label: 'Rebalance Min', data: minRebSeries, borderColor: 'rgba(16, 185, 129, 0.4)', borderWidth: 1, pointRadius: 0, borderDash: [2, 2], fill: false },
                { label: 'Range High', data: maxRangeSeries, borderColor: '#10b981', borderWidth: 1, pointRadius: 0, borderDash: [5, 5] },
                { label: 'Range Low', data: minRangeSeries, borderColor: '#10b981', borderWidth: 1, pointRadius: 0, borderDash: [5, 5] },
                { label: `Only ${baseAsset.symbol.toUpperCase()}`, data: asset1Data.map(d => d[1]), borderColor: '#f59e0b', borderWidth: 1, pointRadius: 0, borderDash: [2, 2] },
                { label: `Only ${quoteAsset.symbol.toUpperCase()}`, data: asset2Data.map(d => d[1]), borderColor: '#ef4444', borderWidth: 1, pointRadius: 0, borderDash: [2, 2] },
                { label: 'HODL', data: hodlData.map(d => d[1]), borderColor: '#9ca3af', borderWidth: 2, pointRadius: 0 },
                { label: 'LP with Fees', data: lpTotalData.map(d => d[1]), borderColor: '#FF007A', backgroundColor: 'rgba(255, 0, 122, 0.1)', borderWidth: 2, pointRadius: 0, fill: true }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}` } } },
            scales: {
                x: { grid: { color: '#2d3748' }, ticks: { color: '#9ca3af', maxTicksLimit: 8 } },
                y: { grid: { color: '#2d3748' }, ticks: { color: '#9ca3af' }, title: { display: true, text: 'Value (100 base)' } }
            }
        }
    });
}

function renderRelativeChart(results, canvas, existingInstance) {
    if (existingInstance) existingInstance.destroy();

    const { relativeSeries } = results;
    const labels = relativeSeries.map(d => new Date(d[0]).toLocaleDateString());

    return new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Return over HODL (%)',
                data: relativeSeries.map(d => d[1]),
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                borderWidth: 2,
                pointRadius: 0,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}%` } },
                legend: { display: false }
            },
            scales: {
                x: { display: false },
                y: {
                    grid: { color: '#2d3748' },
                    ticks: { color: '#9ca3af', callback: (val) => val.toFixed(1) + '%' },
                    title: { display: false }
                }
            }
        }
    });
}

function renderSummaryChart(allResults, canvas, existingInstance) {
    if (existingInstance) existingInstance.destroy();

    const labels = allResults[0].hodlData.map(d => new Date(d[0]).toLocaleDateString());
    const datasets = [];

    // Base performance lines (HODL, Single Assets) - only take from first result
    const first = allResults[0];
    datasets.push({ label: 'HODL', data: first.hodlData.map(d => d[1]), borderColor: '#9ca3af', borderWidth: 2, pointRadius: 0 });
    datasets.push({ label: `Only ${baseAsset.symbol.toUpperCase()}`, data: first.asset1Data.map(d => d[1]), borderColor: '#f59e0b', borderWidth: 1, pointRadius: 0, borderDash: [2, 2] });
    datasets.push({ label: `Only ${quoteAsset.symbol.toUpperCase()}`, data: first.asset2Data.map(d => d[1]), borderColor: '#ef4444', borderWidth: 1, pointRadius: 0, borderDash: [2, 2] });

    // LP lines for each strategy
    const colors = ['#FF007A', '#3b82f6', '#10b981', '#a855f7', '#ec4899', '#06b6d4'];
    allResults.forEach((res, idx) => {
        datasets.push({
            label: `LP: ${res.name}`,
            data: res.lpTotalData.map(d => d[1]),
            borderColor: colors[idx % colors.length],
            borderWidth: 2,
            pointRadius: 0,
            fill: false
        });
    });

    return new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}` } },
                legend: { position: 'bottom', labels: { color: '#9ca3af', padding: 20 } }
            },
            scales: {
                x: { grid: { color: '#2d3748' }, ticks: { color: '#9ca3af', maxTicksLimit: 12 } },
                y: { grid: { color: '#2d3748' }, ticks: { color: '#9ca3af' }, title: { display: true, text: 'Value (100 base)' } }
            }
        }
    });
}

// --- Modal Logic ---

function handleEnlarge(btn) {
    const type = btn.dataset.chartType;
    const block = btn.closest('.strategy-block');
    const summarySection = btn.closest('.summary-section');

    let results = null;
    let title = "";
    let renderFn = null;

    if (summarySection) {
        results = lastCalculatedResults;
        title = "Combined Performance Summary";
        renderFn = renderSummaryChart;
    } else if (block) {
        const id = parseInt(block.dataset.id);
        const strategyName = block.querySelector('.strategy-title-input').value;
        // Find the full results object for this strategy
        // We need to match by name or find a way to store results by ID
        // Let's quickly store them in the strategies array during run
        const strategy = strategies.find(s => s.id === id);
        if (strategy && strategy.lastResults) {
            results = strategy.lastResults;
            if (type === 'main') {
                title = `${strategyName} - Price & LP Range`;
                renderFn = renderChart;
            } else {
                title = `${strategyName} - Relative Return over HODL (%)`;
                renderFn = renderRelativeChart;
            }
        }
    }

    if (results && renderFn) {
        showModal(title);
        const canvas = document.getElementById('enlargedChart');
        enlargedChartInstance = renderFn(results, canvas, enlargedChartInstance);
    }
}

function showModal(title) {
    const modal = document.getElementById('modal-overlay');
    const titleEl = document.getElementById('modal-title');
    titleEl.textContent = title;
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden'; // Prevent scrolling
}

function hideModal() {
    const modal = document.getElementById('modal-overlay');
    modal.classList.add('hidden');
    document.body.style.overflow = '';
    if (enlargedChartInstance) {
        enlargedChartInstance.destroy();
        enlargedChartInstance = null;
    }
}

// Ensure init runs
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
