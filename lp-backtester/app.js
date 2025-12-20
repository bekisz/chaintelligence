// Global State
let baseSearchInput, quoteSearchInput, baseResults, quoteResults, swapBtn;
let aprInput, startDateInput, endDateInput, addStrategyBtn, runBtn;
let allCoins = [];
let baseAsset = { id: 'ethereum', symbol: 'eth', name: 'Ethereum' };
let quoteAsset = { id: 'usd-coin', symbol: 'usdc', name: 'USDC' };

let strategies = [];
let nextStrategyId = 1;

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
    if (baseSearchInput && baseResults) setupSearch(baseSearchInput, baseResults, (coin) => { baseAsset = coin; });
    if (quoteSearchInput && quoteResults) setupSearch(quoteSearchInput, quoteResults, (coin) => { quoteAsset = coin; });

    if (swapBtn) {
        swapBtn.addEventListener('click', () => {
            const temp = baseAsset;
            baseAsset = quoteAsset;
            quoteAsset = temp;
            if (baseSearchInput) baseSearchInput.value = baseAsset.symbol.toUpperCase();
            if (quoteSearchInput) quoteSearchInput.value = quoteAsset.symbol.toUpperCase();
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

            if (toggleBtn) {
                const block = toggleBtn.closest('.strategy-block');
                if (block) block.classList.toggle('collapsed');
            } else if (removeBtn) {
                const block = removeBtn.closest('.strategy-block');
                if (block && block.dataset.id) {
                    removeStrategy(parseInt(block.dataset.id));
                }
            }
        });
    }

    if (runBtn) {
        runBtn.addEventListener('click', updateAllCharts);
    }

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
    addStrategy(); // Start with one strategy
    updateAllCharts(); // Initial run
}

// --- Strategy Management ---

function addStrategy() {
    const container = document.getElementById('strategies-container');
    const template = document.getElementById('strategy-template');
    if (!container || !template) return;

    const clone = template.content.cloneNode(true);
    const block = clone.querySelector('.strategy-block');
    const id = nextStrategyId++;
    block.dataset.id = id;
    block.querySelector('.strategy-title-input').value = `Strategy #${id}`;

    // Elements inside the block
    const strategy = {
        id,
        block,
        minRangeInput: block.querySelector('.min-range'),
        maxRangeInput: block.querySelector('.max-range'),
        rebalanceInput: block.querySelector('.rebalance-select'),
        rebalanceDelayInput: block.querySelector('.rebalance-delay'),
        loading: block.querySelector('.loading-overlay'),
        errorMessage: block.querySelector('.error-message'),
        hodlReturnEl: block.querySelector('.hodl-return'),
        lpReturnEl: block.querySelector('.lp-return'),
        lpDiffEl: block.querySelector('.lp-diff'),
        feesEl: block.querySelector('.fees-collected'),
        canvas: block.querySelector('.strategy-chart'),
        chartInstance: null
    };

    // Event listeners are now handled via delegation in init()
    strategies.push(strategy);
    container.appendChild(clone);

    // If we already have data, we might want to run it, but let's wait for the user to click Run
}

function removeStrategy(id) {
    const index = strategies.findIndex(s => s.id === id);
    if (index === -1) return;

    const strategy = strategies[index];
    if (strategy.chartInstance) strategy.chartInstance.destroy();
    strategy.block.remove();
    strategies.splice(index, 1);
}

// --- Search Logic ---

async function fetchCoinList() {
    try {
        const cached = localStorage.getItem('coingecko_top_100');
        const timestamp = localStorage.getItem('coingecko_top_100_ts');
        const now = Date.now();
        if (cached && timestamp && (now - timestamp < 86400000)) {
            allCoins = JSON.parse(cached);
            return;
        }
        const response = await fetch('https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1&sparkline=false');
        if (!response.ok) throw new Error('Failed to fetch coin list');
        allCoins = await response.json();
        localStorage.setItem('coingecko_top_100', JSON.stringify(allCoins));
        localStorage.setItem('coingecko_top_100_ts', now);
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

        // Process each strategy
        strategies.forEach(s => {
            try {
                const minPct = parseFloat(s.minRangeInput.value) / 100;
                const maxPct = parseFloat(s.maxRangeInput.value) / 100;
                const rebalanceMode = s.rebalanceInput.value || 'none';
                const delayDays = parseInt(s.rebalanceDelayInput.value) || 1;

                const results = calculateV3Backtest(ratioSeries, minPct, maxPct, baseAprPct, rebalanceMode, delayDays);

                s.chartInstance = renderChart(results, s.canvas, s.chartInstance);
                updateStrategyInfo(results, s);
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

function calculateV3Backtest(priceSeries, minPct, maxPct, baseApr, rebalanceMode, delayDays) {
    const P0_initial = priceSeries[0][1];
    let P0 = P0_initial;
    let P_min = P0 * (1 + minPct);
    let P_max = P0 * (1 + maxPct);
    let currentCapital = 100;
    let { L } = getLikidityAndAmounts(P0, P_min, P_max, currentCapital);

    const initialPos = getLikidityAndAmounts(P0, P_min, P_max, 100);
    const initial_x_hodl = initialPos.x, initial_y_hodl = initialPos.y;
    const amt_asset1 = 100 / P0_initial, amt_asset2 = 100;

    const hodlData = [], lpTotalData = [], asset1Data = [], asset2Data = [];
    const minRangeSeries = [], maxRangeSeries = [];
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

        // Rebalance
        let shouldRebalance = false;
        if (P < P_min || P > P_max) daysOutOfRange++;
        else daysOutOfRange = 0;

        if (rebalanceMode === 'immediate' && (P < P_min || P > P_max)) shouldRebalance = true;
        else if (rebalanceMode === 'delayed' && daysOutOfRange >= delayDays) {
            shouldRebalance = true;
            daysOutOfRange = 0;
        }

        if (shouldRebalance) {
            currentCapital = val_lp_principal + accumulatedFees;
            accumulatedFees = 0;
            P0 = P;
            P_min = P0 * (1 + minPct);
            P_max = P0 * (1 + maxPct);
            L = getLikidityAndAmounts(P0, P_min, P_max, currentCapital).L;
            val_lp_principal = currentCapital;
        }

        lpTotalData.push([time, val_lp_principal + accumulatedFees]);
        minRangeSeries.push((P_min / P0_initial) * 100);
        maxRangeSeries.push((P_max / P0_initial) * 100);
    }
    return { hodlData, lpTotalData, asset1Data, asset2Data, accumulatedFees, minRangeSeries, maxRangeSeries };
}

function renderChart(results, canvas, existingInstance) {
    if (existingInstance) existingInstance.destroy();

    const { hodlData, lpTotalData, asset1Data, asset2Data, minRangeSeries, maxRangeSeries } = results;
    const labels = hodlData.map(d => new Date(d[0]).toLocaleDateString());

    return new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels,
            datasets: [
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

function updateStrategyInfo(results, s) {
    const { hodlData, lpTotalData, accumulatedFees } = results;
    if (!hodlData.length) return;
    const endHodl = hodlData[hodlData.length - 1][1], endLp = lpTotalData[lpTotalData.length - 1][1];
    const hodlRet = endHodl - 100, lpRet = endLp - 100, diff = endLp - endHodl;

    s.hodlReturnEl.textContent = `${hodlRet > 0 ? '+' : ''}${hodlRet.toFixed(2)}%`;
    s.hodlReturnEl.className = 'value ' + (hodlRet >= 0 ? 'positive' : 'negative');
    s.lpReturnEl.textContent = `${lpRet > 0 ? '+' : ''}${lpRet.toFixed(2)}%`;
    s.lpReturnEl.className = 'value ' + (lpRet >= 0 ? 'positive' : 'negative');
    s.lpDiffEl.textContent = `${diff > 0 ? '+' : ''}${diff.toFixed(2)}%`;
    s.lpDiffEl.className = 'value ' + (diff >= 0 ? 'positive' : 'negative');
    s.feesEl.textContent = `+${accumulatedFees.toFixed(2)}`;
}

// Ensure init runs
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
