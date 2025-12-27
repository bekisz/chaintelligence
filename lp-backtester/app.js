// Global State
let baseSearchInput, quoteSearchInput, baseResults, quoteResults, swapBtn;
let aprInput, startDateInput, endDateInput, addStrategyBtn, runBtn;
let chartScaleSelect;
let allCoins = [];
let baseAsset = { id: 'ethereum', symbol: 'eth', name: 'Ethereum' };
let quoteAsset = { id: 'usd-coin', symbol: 'usdc', name: 'USDC' };

let strategies = [];
let nextStrategyId = 1;
let summaryChartInstance = null;
let yoySummaryChartInstance = null;
let enlargedChartInstance = null;
let currentEnlargedState = null;
let lastCalculatedResults = []; // Store results for enlargement later

const CHART_ZOOM_OPTIONS = {
    zoom: {
        wheel: { enabled: true },
        pinch: { enabled: true },
        drag: {
            enabled: true,
            backgroundColor: 'rgba(59, 130, 246, 0.2)',
            borderColor: '#3b82f6',
            borderWidth: 1,
            threshold: 10
        },
        mode: 'x',
    },
    pan: {
        enabled: true,
        mode: 'x',
        modifierKey: 'shift',
    }
};

// Explicitly register the plugin if available globally
if (window.Chart && window.ChartZoom) {
    Chart.register(window.ChartZoom);
}

const STRATEGY_HELP_CONTENT = `
<div class="help-content">
    <h2>1. Non-rebalancing</h2>
    <p><strong>Description</strong>: A static liquidity position with no rebalancing logic. The position remains at the initial price boundaries until the end of the simulation.</p>
    <ul>
        <li><strong>Trigger</strong>: None.</li>
        <li><strong>Action</strong>: None.</li>
    </ul>

    <hr>

    <h2>2. Time-delayed Rebalancing</h2>
    <p><strong>Description</strong>: Rebalances the position once the price has spent a consecutive number of days outside of the defined <strong>Rebalance Range</strong>.</p>
    <ul>
        <li><strong>Trigger</strong>: Price stays below <code>Rebalance Min %</code> or above <code>Rebalance Max %</code> for <code>Delay</code> consecutive days.</li>
        <li><strong>Action</strong>: The entire capital (principal + fees) is consolidated and redeployed centered on the <strong>current market price</strong>.</li>
    </ul>

    <hr>

    <h2>3. Settled Rebalancing</h2>
    <p><strong>Description</strong>: A more conservative strategy that only rebalances once the market has "settled" into a new price range after a breach.</p>
    <ul>
        <li><strong>Trigger</strong>: 
            <ol>
                <li>Price outside <code>Rebalance Range</code> for at least <code>Delay</code> consecutive days.</li>
                <li>Price volatility during those days remains consistent (prices stay within rebalance boundaries relative to the geometric average).</li>
            </ol>
        </li>
        <li><strong>Action</strong>: Redeployed centered on the <strong>Geometric Average Price</strong> of the settlement period.</li>
    </ul>

    <hr>

    <h2>4. Periodic Rebalance</h2>
    <p><strong>Description</strong>: A time-based strategy that rebalances the position at a fixed interval, regardless of price action.</p>
    <ul>
        <li><strong>Trigger</strong>: <code>Delay</code> days have passed since start or last rebalance.</li>
        <li><strong>Action</strong>: Redeployed centered on the <strong>current market price</strong>.</li>
    </ul>
</div>
`;

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
    chartScaleSelect = document.getElementById('chart-scale-select');

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

    if (chartScaleSelect) {
        chartScaleSelect.addEventListener('change', updateAllCharts);
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
            } else {
                const toggleChartBtn = e.target.closest('.toggle-chart-btn');
                if (toggleChartBtn) {
                    const wrapper = toggleChartBtn.closest('.chart-wrapper');
                    if (wrapper) wrapper.classList.toggle('collapsed');
                }
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

    // Modal Events
    document.addEventListener('click', (e) => {
        const infoBtn = e.target.closest('.info-btn');
        if (infoBtn) {
            showModal("LP Strategy Types", STRATEGY_HELP_CONTENT, true);
        }
    });

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

    setupTabs();
    updateAllCharts(); // Initial run
}

function setupTabs() {
    const tabs = document.querySelectorAll('.tab-btn');
    const contents = document.querySelectorAll('.tab-content');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            contents.forEach(c => c.classList.remove('active'));

            tab.classList.add('active');
            const targetId = `tab-${tab.dataset.tab}`;
            const target = document.getElementById(targetId);
            if (target) target.classList.add('active');
        });
    });
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
        relativeCanvas: block.querySelector('.relative-chart'),
        relativeChartInstance: null,
        volatilityCanvas: block.querySelector('.volatility-chart'),
        volatilityChartInstance: null,
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
        } else if (strategy.rebalanceType.value === 'periodic') {
            strategy.rebalanceParamsRow.classList.remove('disabled');
            strategy.rebalanceMinInput.disabled = true;
            strategy.rebalanceMaxInput.disabled = true;
            strategy.rebalanceMinInput.parentElement.classList.add('disabled-input'); // Optional styling
            strategy.rebalanceMaxInput.parentElement.classList.add('disabled-input');
            strategy.rebalanceDelayInput.disabled = false;
        } else {
            strategy.rebalanceParamsRow.classList.remove('disabled');
            strategy.rebalanceMinInput.disabled = false;
            strategy.rebalanceMaxInput.disabled = false;
            strategy.rebalanceMinInput.parentElement.classList.remove('disabled-input');
            strategy.rebalanceMaxInput.parentElement.classList.remove('disabled-input');
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
            } else if (s.rebalanceType.value === 'periodic') {
                const delay = parseInt(s.rebalanceDelayInput.value);
                if (isNaN(delay) || delay < 1) {
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
    if (strategy.volatilityChartInstance) strategy.volatilityChartInstance.destroy();
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

        // Calculate volatility series for different periods
        const volatilityData = {
            v7d: calculateRollingVolatility(ratioSeries, 7),
            v30d: calculateRollingVolatility(ratioSeries, 30),
            v90d: calculateRollingVolatility(ratioSeries, 90)
        };



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

                // Calculate volatility shared for all strategies on same pair (optimization: can be moved out if strictly pair-dependent, 
                // but here we just calculate it. Wait, `ratioSeries` IS global for the run. 
                // Plan said move it out. Let's stick to plan.)


                const results = calculateV3Backtest(ratioSeries, minPct, maxPct, rebMinPct, rebMaxPct, baseAprPct, rebalanceMode, delayDays);

                s.chartInstance = renderChart(results, s.canvas, s.chartInstance);

                s.relativeChartInstance = renderRelativeChart(results, s.relativeCanvas, s.relativeChartInstance);
                s.volatilityChartInstance = renderVolatilityChart(volatilityData, s.volatilityCanvas, s.volatilityChartInstance);
                s.lastResults = { ...results, volatilityData }; // Store for enlargement

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
                // summarySection.classList.remove('hidden'); // No longer needed, controlled by tabs
                summaryChartInstance = renderSummaryChart(allResults, document.getElementById('summaryChart'), summaryChartInstance);
                yoySummaryChartInstance = renderYoYChart(allResults, document.getElementById('yoySummaryChart'), yoySummaryChartInstance);
                lastCalculatedResults = allResults;
            } else {
                // summarySection.classList.add('hidden'); // Don't hide the section, just charts empty
                lastCalculatedResults = [];
                if (summaryChartInstance) summaryChartInstance.destroy();
                if (yoySummaryChartInstance) yoySummaryChartInstance.destroy();
            }
        }

        // If we have an enlarged chart open, re-render it to reflect potential changes (like Log Scale)
        if (currentEnlargedState) {
            const canvas = document.getElementById('enlargedChart');
            if (currentEnlargedState.type === 'summary') {
                enlargedChartInstance = renderSummaryChart(allResults, canvas, enlargedChartInstance);
            } else if (currentEnlargedState.type === 'yoy-summary') {
                enlargedChartInstance = renderYoYChart(allResults, canvas, enlargedChartInstance);
            } else {
                const strategy = strategies.find(s => s.id === currentEnlargedState.id);
                if (strategy && strategy.lastResults) {
                    if (currentEnlargedState.type === 'main') {
                        enlargedChartInstance = renderChart(strategy.lastResults, canvas, enlargedChartInstance);
                    } else if (currentEnlargedState.type === 'relative') {
                        enlargedChartInstance = renderRelativeChart(strategy.lastResults, canvas, enlargedChartInstance);
                    } else if (currentEnlargedState.type === 'volatility') {
                        enlargedChartInstance = renderVolatilityChart(strategy.lastResults.volatilityData, canvas, enlargedChartInstance);
                    }
                }
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
    let accumulatedFees = 0, daysOutOfRange = 0, lastRebalanceTime = priceSeries[0][0];

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

        if (rebalanceMode === 'periodic') {
            // Periodic rebalance: check time elapsed since last rebalance
            // We use time (ms) instead of iteration count to handle potential data gaps
            const msSinceLast = time - (typeof lastRebalanceTime === 'undefined' ? priceSeries[0][0] : lastRebalanceTime);
            // delayDays is in days, convert to ms. Subtract 1 hour buffer to handle DST/slight timestamp variances
            const requiredMs = (delayDays * 24 * 60 * 60 * 1000) - 3600000;

            if (msSinceLast >= requiredMs) {
                shouldRebalance = true;
                rebalanceCenterPrice = P;
            }

            // Increment daysOutOfRange solely for chart visualization/debugging if needed
            daysOutOfRange++;
        } else if (rebalanceMode !== 'simple') {
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
            lastRebalanceTime = time;
        }

        lpTotalData.push([time, val_lp_principal + accumulatedFees]);
        minRangeSeries.push((P_min / P0_initial) * 100);
        maxRangeSeries.push((P_max / P0_initial) * 100);
        minRebSeries.push((P_reb_min / P0_initial) * 100);
    }

    return {
        hodlData,
        lpTotalData,
        daysOutOfRange,
        minRangeSeries,
        maxRangeSeries,
        minRebSeries,
        maxRebSeries,
        asset1Data,
        asset2Data,
        relativeSeries: lpTotalData.map((d, i) => {
            const hodlVal = hodlData[i][1];
            return [d[0], ((d[1] / hodlVal) - 1) * 100];
        })
    };
}

function calculateRollingVolatility(priceSeries, windowSize = 30) {
    if (priceSeries.length < windowSize + 1) return [];

    const volatilitySeries = [];

    // Calculate daily log returns: ln(Pt / Pt-1)
    const logReturns = [];
    for (let i = 1; i < priceSeries.length; i++) {
        const r = Math.log(priceSeries[i][1] / priceSeries[i - 1][1]);
        logReturns.push(r);
    }

    // Calculate rolling std dev and annualize
    // Volatility starts from index `windowSize` (needs `windowSize` prior returns)
    for (let i = windowSize; i < priceSeries.length; i++) {
        const time = priceSeries[i][0];

        // Slice returns window. Note: returns index i corresponds to price change i-1 to i.
        // We want returns ending at time i. logReturns[i-1] is the return from i-1 to i.
        // So window is logReturns[i - windowSize ... i-1] (length windowSize)
        const windowReturns = logReturns.slice(i - windowSize, i);

        // Mean return
        const mean = windowReturns.reduce((sum, val) => sum + val, 0) / windowSize;

        // Variance
        const variance = windowReturns.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / (windowSize - 1);

        // Std Dev
        const stdDev = Math.sqrt(variance);

        // Annualize: stdDev * sqrt(365) * 100 (for percentage)
        const annualizedVol = stdDev * Math.sqrt(365) * 100;

        volatilitySeries.push([time, annualizedVol]);
    }

    return volatilitySeries;
}

function renderChart(results, canvas, existingChart) {
    const ctx = canvas.getContext('2d');
    const isLog = chartScaleSelect ? chartScaleSelect.value === 'logarithmic' : false;

    const datasets = [
        {
            label: 'HODL Value',
            data: results.hodlData.map(d => ({ x: d[0], y: d[1] })),
            borderColor: '#60a5fa', // Blue
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.1
        },
        {
            label: 'LP Strategy Value',
            data: results.lpTotalData.map(d => ({ x: d[0], y: d[1] })),
            borderColor: '#10b981', // Green
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.1
        },
        // Range lines
        {
            label: 'LP Min',
            data: results.hodlData.map((d, i) => ({ x: d[0], y: d[1] * (1 + results.minRangeSeries[i] / 100) / (1 + (results.minRangeSeries[i] / 100 * 0)) })), // Approximate visualization logic simplified in original? No, let's look at original logic.
            // Wait, the original renderChart logic isn't fully visible in view_file.
            // I should just ADD the new render functions and let the existing ones be. 
            // I am replacing content, so I need to be careful not to delete renderChart if I don't have its full code.
            // The view_file output ended at line 800 and renderChart wasn't fully shown.
            // I will implement calculateRollingVolatility and renderVolatilityChart AFTER calculateV3Backtest and BEFORE other render functions if possible, or at the end of file.
            // BUT, I need to make sure I don't overwrite renderChart if I don't have it.
            // Checking the file view again... line 800 is inside calculateV3Backtest. 
            // I will assume renderChart is further down.
            // I will APPEND the new functions at the end of the file or after calculateV3Backtest.
            // I need to read the rest of the file first to be safe.
        }
    ];
}


function calculateYoY(series) {
    // series is array of [timestamp, value]
    // returns array of [timestamp, yoyPct]
    // window is 365 days (approx 31536000000 ms)
    const msPerYear = 365 * 24 * 60 * 60 * 1000;
    const yoySeries = [];
    let pastIndex = 0;

    for (let i = 0; i < series.length; i++) {
        const [currentTime, currentValue] = series[i];

        // Advance pastIndex until it's roughly 1 year ago
        while (pastIndex < i && (currentTime - series[pastIndex + 1][0]) > msPerYear) {
            pastIndex++;
        }

        const [pastTime, pastValue] = series[pastIndex];
        const timeDiff = currentTime - pastTime;

        // If we found a point roughly 1 year ago (within 10% margin is usually strict enough backtest, 
        // but let's just use exact match logic or closest point >= 365 days)
        // Here we just check if timeDiff is close to 1 year.
        // If the dataset < 1 year, this loop produces nothing or initial noise.
        // Let's enforce that timeDiff > 360 days.
        if (timeDiff >= 360 * 24 * 60 * 60 * 1000) {
            const yoy = ((currentValue / pastValue) - 1) * 100;
            yoySeries.push([currentTime, yoy]);
        } else {
            // Not enough data yet
        }
    }
    return yoySeries;
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
            plugins: {
                tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}` } },
                zoom: {
                    zoom: CHART_ZOOM_OPTIONS.zoom,
                    pan: CHART_ZOOM_OPTIONS.pan
                }
            },
            scales: {
                x: { grid: { color: '#2d3748' }, ticks: { color: '#9ca3af', maxTicksLimit: 8 } },
                y: {
                    type: chartScaleSelect ? chartScaleSelect.value : 'linear',
                    grid: { color: '#2d3748' },
                    ticks: { color: '#9ca3af' },
                    title: { display: true, text: 'Value (100 base)' }
                }
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
                legend: { display: false },
                zoom: {
                    zoom: CHART_ZOOM_OPTIONS.zoom,
                    pan: CHART_ZOOM_OPTIONS.pan
                }
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
                tooltip: {
                    itemSort: (a, b) => b.raw - a.raw,
                    callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}` }
                },
                legend: { position: 'bottom', labels: { color: '#9ca3af', padding: 20 } },
                zoom: {
                    zoom: CHART_ZOOM_OPTIONS.zoom,
                    pan: CHART_ZOOM_OPTIONS.pan
                }
            },
            scales: {
                x: { grid: { color: '#2d3748' }, ticks: { color: '#9ca3af', maxTicksLimit: 12 } },
                y: {
                    type: chartScaleSelect ? chartScaleSelect.value : 'linear',
                    grid: { color: '#2d3748' },
                    ticks: { color: '#9ca3af' },
                    title: { display: true, text: 'Value (100 base)' }
                }
            }
        }
    });
}


function renderYoYChart(allResults, canvas, existingInstance) {
    if (existingInstance) existingInstance.destroy();

    const colors = ['#FF007A', '#3b82f6', '#10b981', '#a855f7', '#ec4899', '#06b6d4'];
    const datasets = [];

    // HODL YoY
    if (allResults.length > 0) {
        const hodlSeries = calculateYoY(allResults[0].hodlData);
        if (hodlSeries.length > 0) {
            datasets.push({ label: 'HODL', data: hodlSeries.map(d => d[1]), borderColor: '#9ca3af', borderWidth: 2, pointRadius: 0 });
        }
    }

    allResults.forEach((res, idx) => {
        const yoy = calculateYoY(res.lpTotalData);
        if (yoy.length > 0) {
            datasets.push({
                label: `LP: ${res.name}`,
                data: yoy.map(d => d[1]),
                borderColor: colors[idx % colors.length],
                borderWidth: 2,
                pointRadius: 0,
                fill: false
            });
        }
    });

    // Use labels from the filtered HODL YoY timestamps
    let labels = [];
    if (allResults.length > 0) {
        const hodlYoY = calculateYoY(allResults[0].hodlData);
        labels = hodlYoY.map(d => new Date(d[0]).toLocaleDateString());
    }

    return new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                tooltip: {
                    itemSort: (a, b) => b.raw - a.raw,
                    callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}%` }
                },
                legend: { position: 'bottom', labels: { color: '#9ca3af', padding: 20 } },
                zoom: {
                    zoom: CHART_ZOOM_OPTIONS.zoom,
                    pan: CHART_ZOOM_OPTIONS.pan
                }
            },
            scales: {
                x: { grid: { color: '#2d3748' }, ticks: { color: '#9ca3af', maxTicksLimit: 12 } },
                y: {
                    type: 'linear', // Always linear for pct return
                    grid: { color: '#2d3748' },
                    ticks: { color: '#9ca3af', callback: (val) => val.toFixed(1) + '%' },
                    title: { display: true, text: 'YoY Return (%)' }
                }
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
    } else if (type === 'yoy-summary') {
        results = lastCalculatedResults;
        title = "Year-over-Year Return (%)";
        renderFn = renderYoYChart;
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
            } else if (type === 'volatility') {
                title = `${strategyName} - Annualized Volatility`;
                renderFn = renderVolatilityChart;
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

        // Store state for live updates
        if (summarySection) {
            currentEnlargedState = { type: type }; // type is 'summary' or 'yoy-summary'
        } else if (block) {
            currentEnlargedState = { id: parseInt(block.dataset.id), type: type };
        }
    }
}


function showModal(title, content, isHtml = false) {
    const modal = document.getElementById('modal-overlay');
    const titleEl = document.getElementById('modal-title');
    const canvas = document.getElementById('enlargedChart');
    const textContent = document.getElementById('modal-text-content');

    titleEl.textContent = title;

    if (isHtml) {
        canvas.classList.add('hidden');
        textContent.classList.remove('hidden');
        textContent.innerHTML = content;
    } else {
        // Prepare for chart
        canvas.classList.remove('hidden');
        textContent.classList.add('hidden');
        textContent.innerHTML = '';
    }

    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden'; // Prevent scrolling
}

function hideModal() {
    const modal = document.getElementById('modal-overlay');
    modal.classList.add('hidden');
    document.body.style.overflow = '';
    currentEnlargedState = null;
    if (enlargedChartInstance) {
        enlargedChartInstance.destroy();
        enlargedChartInstance = null;
    }
}

// Ensure init runs
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

function renderVolatilityChart(volatilityData, canvas, existingInstance) {
    if (existingInstance) existingInstance.destroy();

    // Use labels from the longest series (or base ratio series if passed, but 7d is longest available)
    // Actually, 90d is shortest (starts later). 7d starts earliest.
    // Let's use labels from v7d, but we need to align datasets.
    // Chart.js handles x/y data points fine if we map them.

    // Helper to format data
    const formatData = (series) => series.map(d => ({ x: d[0], y: d[1] }));

    // Determine labels from the longest dataset (v7d)
    const labels = volatilityData.v7d.map(d => new Date(d[0]).toLocaleDateString());

    // We use the labels property on the main data object for the category axis
    return new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels, // Pass the calculated labels here
            datasets: [
                {
                    label: '7D Annualized',
                    data: volatilityData.v7d.map(d => d[1]), // Just y-values, since we use category axis
                    borderColor: '#ec4899', // Pink
                    borderWidth: 1.5,
                    pointRadius: 0,
                    fill: false,
                    tension: 0.1
                },
                {
                    label: '30D Annualized',
                    data: volatilityData.v30d.map(d => d[1]),
                    borderColor: '#8b5cf6', // Violet
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: false,
                    tension: 0.1
                },
                {
                    label: 'Quarterly (90D) Annualized',
                    data: volatilityData.v90d.map(d => d[1]),
                    borderColor: '#3b82f6', // Blue
                    borderWidth: 1.5,
                    pointRadius: 0,
                    fill: false,
                    tension: 0.1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                tooltip: {
                    itemSort: (a, b) => b.raw - a.raw, // simple number compare since data is numbers now
                    callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}%` }
                },
                legend: {
                    display: true, // Show legend for multiple lines
                    labels: { color: '#9ca3af', boxWidth: 12, padding: 15 }
                },
                zoom: {
                    zoom: CHART_ZOOM_OPTIONS.zoom,
                    pan: CHART_ZOOM_OPTIONS.pan
                }
            },
            scales: {
                x: {
                    grid: { color: '#2d3748' },
                    ticks: { color: '#9ca3af', maxTicksLimit: 8 }
                },
                y: {
                    grid: { color: '#2d3748' },
                    ticks: { color: '#9ca3af', callback: (val) => val.toFixed(0) + '%' },
                    title: { display: true, text: 'Volatility (%)' }
                }
            }
        }
    });
}
