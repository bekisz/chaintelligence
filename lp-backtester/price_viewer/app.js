const baseSearchInput = document.getElementById('base-search');
const quoteSearchInput = document.getElementById('quote-search');
const baseResults = document.getElementById('base-results');
const quoteResults = document.getElementById('quote-results');
const swapBtn = document.getElementById('swap-btn');

// V3 Inputs
const minRangeInput = document.getElementById('min-range');
const maxRangeInput = document.getElementById('max-range');
const aprInput = document.getElementById('apr-input');
const startDateInput = document.getElementById('start-date');
const endDateInput = document.getElementById('end-date');
const rebalanceInput = document.getElementById('rebalance-select');
const runBtn = document.getElementById('run-btn');

const loading = document.getElementById('loading');
const errorMessage = document.getElementById('error-message');

// Info Elements
const hodlReturnEl = document.getElementById('hodl-return');
const lpReturnEl = document.getElementById('lp-return');
const lpDiffEl = document.getElementById('lp-diff');
const feesEl = document.getElementById('fees-collected');

let chartInstance = null;
let allCoins = [];

// Defaults
let baseAsset = { id: 'ethereum', symbol: 'eth', name: 'Ethereum' };
let quoteAsset = { id: 'usd-coin', symbol: 'usdc', name: 'USDC' };

// --- Initialization ---

async function init() {
    if (baseSearchInput) baseSearchInput.value = baseAsset.symbol.toUpperCase();
    if (quoteSearchInput) quoteSearchInput.value = quoteAsset.symbol.toUpperCase();

    // Default start date: 2 years ago
    if (startDateInput) {
        const d = new Date();
        d.setFullYear(d.getFullYear() - 2);
        startDateInput.valueAsDate = d;
    }

    // Default end date: Today
    if (endDateInput) {
        endDateInput.valueAsDate = new Date();
    }

    // Load coin list
    await fetchCoinList();

    updateChart(); // Run initial backtest
}

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
        try {
            localStorage.setItem('coingecko_top_100', JSON.stringify(allCoins));
            localStorage.setItem('coingecko_top_100_ts', now);
        } catch (e) { }
    } catch (error) {
        console.error("Error loading coins:", error);
    }
}

// --- Search Logic ---

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
            // Don't auto-update chart on selection, wait for Run
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

setupSearch(baseSearchInput, baseResults, (coin) => { baseAsset = coin; });
setupSearch(quoteSearchInput, quoteResults, (coin) => { quoteAsset = coin; });

// --- Swap Logic ---

if (swapBtn) {
    swapBtn.addEventListener('click', () => {
        const temp = baseAsset;
        baseAsset = quoteAsset;
        quoteAsset = temp;
        baseSearchInput.value = baseAsset.symbol.toUpperCase();
        quoteSearchInput.value = quoteAsset.symbol.toUpperCase();
    });
}

if (runBtn) {
    runBtn.addEventListener('click', updateChart);
}

// --- Chart Data & Rendering ---

async function updateChart() {
    if (!baseAsset || !quoteAsset) return;

    if (loading) loading.classList.add('active');
    if (errorMessage) {
        errorMessage.classList.add('hidden');
        errorMessage.textContent = '';
    }

    try {
        const apiKey = CONFIG.CRYPTOCOMPARE_API_KEY;
        const startDatePoints = new Date(startDateInput.value).getTime();

        // End date points (default to now if empty, or end of the selected day)
        let endDatePoints = Date.now();
        if (endDateInput && endDateInput.value) {
            const d = new Date(endDateInput.value);
            d.setHours(23, 59, 59, 999); // Include the full end day
            endDatePoints = d.getTime();
        }

        // 1. Fetch MAX history (daily) for both assets
        const baseData = await fetchHistory(baseAsset.symbol.toUpperCase(), apiKey);
        const quoteData = await fetchHistory(quoteAsset.symbol.toUpperCase(), apiKey);

        if (!baseData || !quoteData) throw new Error("Missing data");

        // 2. Calculate Price Ratio (Base/Quote) Series
        let ratioSeries = calculateRatioSeries(baseData, quoteData);

        // 3. Filter by Start and End Date
        ratioSeries = ratioSeries.filter(p => p[0] >= startDatePoints && p[0] <= endDatePoints);

        if (ratioSeries.length === 0) throw new Error("No data for selected date range");

        // 4. Calculate V3 Position vs HODL
        const minPct = parseFloat(minRangeInput.value) / 100;
        const maxPct = parseFloat(maxRangeInput.value) / 100;
        const aprPct = parseFloat(aprInput.value) / 100;
        const rebalanceMode = rebalanceInput ? rebalanceInput.value : 'none';

        const results = calculateV3Backtest(ratioSeries, minPct, maxPct, aprPct, rebalanceMode);

        renderChart(results, ratioSeries);
        updateInfo(results);

    } catch (error) {
        console.error(error);
        if (errorMessage) {
            errorMessage.textContent = error.message;
            errorMessage.classList.remove('hidden');
        } else {
            alert(`Error: ${error.message}`);
        }
    } finally {
        if (loading) loading.classList.remove('active');
    }
}

async function fetchHistory(symbol, apiKey) {
    // Always fetch MAX daily data ('allData=true') to ensure we cover the start date
    // User can filter client-side
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
        if (qPrice !== 0) {
            series.push([time, bPrice / qPrice]);
        }
    }
    return series;
}

// --- V3 Math ---

function getLikidityAndAmounts(P, P_min, P_max, V_target) {
    const sqrtP = Math.sqrt(P);
    const sqrtPa = Math.sqrt(P_min);
    const sqrtPb = Math.sqrt(P_max);

    let x_L1 = 0;
    let y_L1 = 0;

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

function calculateV3Backtest(priceSeries, minPct, maxPct, apr, rebalanceMode) {
    const P0_initial = priceSeries[0][1];

    let P0 = P0_initial;
    let P_min = P0 * (1 + minPct);
    let P_max = P0 * (1 + maxPct);

    let currentCapital = 100;

    let { L } = getLikidityAndAmounts(P0, P_min, P_max, currentCapital);

    // HODL Trackers (Reference is initial capital held in initial composition)
    const initial_amounts = getLikidityAndAmounts(P0, P_min, P_max, 100);
    const initial_x_hodl = initial_amounts.x;
    const initial_y_hodl = initial_amounts.y;

    const amt_asset1 = 100 / P0_initial;
    const amt_asset2 = 100;

    const hodlData = [];
    const lpTotalData = [];
    const asset1Data = [];
    const asset2Data = [];

    const minRangeSeries = [];
    const maxRangeSeries = [];

    let accumulatedFees = 0;

    for (let i = 0; i < priceSeries.length; i++) {
        const [time, P] = priceSeries[i];

        // 1. HODL Value
        const val_hodl = (initial_x_hodl * P + initial_y_hodl);
        hodlData.push([time, val_hodl]);

        // 2. Asset Only Values
        asset1Data.push([time, (amt_asset1 * P)]);
        asset2Data.push([time, amt_asset2]);

        // 3. LP Logic
        const sqrtP = Math.sqrt(P);
        const sqrtPa = Math.sqrt(P_min);
        const sqrtPb = Math.sqrt(P_max);

        let x_t = 0;
        let y_t = 0;
        let inRange = false;

        if (P < P_min) {
            x_t = (1 / sqrtPa - 1 / sqrtPb) * L;
            y_t = 0;
        } else if (P > P_max) {
            y_t = (sqrtPb - sqrtPa) * L;
            x_t = 0;
        } else {
            x_t = (1 / sqrtP - 1 / sqrtPb) * L;
            y_t = (sqrtP - sqrtPa) * L;
            inRange = true;
        }

        let val_lp_principal = x_t * P + y_t;

        // Fees
        if (i > 0) {
            const prevTime = priceSeries[i - 1][0];
            const yearsElapsed = (time - prevTime) / (1000 * 60 * 60 * 24 * 365);
            if (inRange) {
                const fees = val_lp_principal * apr * yearsElapsed;
                accumulatedFees += fees;
            }
        }

        // --- Rebalance Check ---
        if (rebalanceMode === 'immediate') {
            if (P < P_min || P > P_max) {
                // REBALANCE
                currentCapital = val_lp_principal + accumulatedFees;
                accumulatedFees = 0;

                P0 = P;
                P_min = P0 * (1 + minPct);
                P_max = P0 * (1 + maxPct);

                const newPos = getLikidityAndAmounts(P0, P_min, P_max, currentCapital);
                L = newPos.L;

                val_lp_principal = currentCapital;
            }
        }

        lpTotalData.push([time, val_lp_principal + accumulatedFees]);

        minRangeSeries.push((P_min / P0_initial) * 100);
        maxRangeSeries.push((P_max / P0_initial) * 100);
    }

    return { hodlData, lpTotalData, asset1Data, asset2Data, accumulatedFees, minRangeSeries, maxRangeSeries };
}

function renderChart(results, priceSeries) {
    const ctx = document.getElementById('priceChart').getContext('2d');
    const { hodlData, lpTotalData, asset1Data, asset2Data, minRangeSeries, maxRangeSeries } = results;

    const labels = hodlData.map(d => new Date(d[0]).toLocaleDateString());
    const hodlPoints = hodlData.map(d => d[1]);
    const lpTotalPoints = lpTotalData.map(d => d[1]);
    const asset1Points = asset1Data.map(d => d[1]);
    const asset2Points = asset2Data.map(d => d[1]);

    // Use dynamic range series provided by calculation
    const rangeLowPoints = minRangeSeries;
    const rangeHighPoints = maxRangeSeries;

    if (chartInstance) chartInstance.destroy();

    chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Range High',
                    data: rangeHighPoints,
                    borderColor: '#10b981', // Green
                    borderWidth: 1,
                    pointRadius: 0,
                    borderDash: [5, 5],
                    pointHitRadius: 0,
                    yAxisID: 'y'
                },
                {
                    label: 'Range Low',
                    data: rangeLowPoints,
                    borderColor: '#10b981', // Green
                    borderWidth: 1,
                    pointRadius: 0,
                    borderDash: [5, 5],
                    pointHitRadius: 0,
                    yAxisID: 'y'
                },
                {
                    label: `Only ${baseAsset.symbol.toUpperCase()}`,
                    data: asset1Points,
                    borderColor: '#f59e0b', // Amber/Orange
                    borderWidth: 1,
                    pointRadius: 0,
                    tension: 0.1,
                    borderDash: [2, 2],
                    yAxisID: 'y'
                },
                {
                    label: `Only ${quoteAsset.symbol.toUpperCase()}`,
                    data: asset2Points,
                    borderColor: '#ef4444', // Red
                    borderWidth: 1,
                    pointRadius: 0,
                    tension: 0.1,
                    borderDash: [2, 2],
                    yAxisID: 'y'
                },
                {
                    label: 'HODL',
                    data: hodlPoints,
                    borderColor: '#9ca3af', // Grey
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.1,
                    yAxisID: 'y'
                },
                {
                    label: 'LP with Fees',
                    data: lpTotalPoints,
                    borderColor: '#FF007A', // Uniswap Pink
                    backgroundColor: 'rgba(255, 0, 122, 0.1)',
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: true,
                    tension: 0.1,
                    yAxisID: 'y'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: function (ctx) {
                            return `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}`;
                        }
                    }
                }
            },
            scales: {
                x: { grid: { color: '#2d3748', drawBorder: false }, ticks: { color: '#9ca3af', maxTicksLimit: 8 } },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    grid: { color: '#2d3748', drawBorder: false },
                    ticks: { color: '#9ca3af', callback: (v) => v.toFixed(0) },
                    title: { display: true, text: 'Portfolio Value (100 base)', color: '#6b7280' }
                }
            }
        }
    });
}

function updateInfo(results) {
    const { hodlData, lpTotalData, accumulatedFees } = results;
    if (!hodlData.length) return;

    const endHodl = hodlData[hodlData.length - 1][1];
    const endLpTotal = lpTotalData[lpTotalData.length - 1][1];

    const hodlRet = endHodl - 100;
    const lpRet = endLpTotal - 100;
    const diff = endLpTotal - endHodl;

    if (hodlReturnEl) {
        hodlReturnEl.textContent = `${hodlRet > 0 ? '+' : ''}${hodlRet.toFixed(2)}%`;
        hodlReturnEl.className = 'value ' + (hodlRet >= 0 ? 'positive' : 'negative');
    }

    if (lpReturnEl) {
        lpReturnEl.textContent = `${lpRet > 0 ? '+' : ''}${lpRet.toFixed(2)}%`;
        lpReturnEl.className = 'value ' + (lpRet >= 0 ? 'positive' : 'negative');
    }

    if (lpDiffEl) {
        lpDiffEl.textContent = `${diff > 0 ? '+' : ''}${diff.toFixed(2)}%`;
        lpDiffEl.className = 'value ' + (diff >= 0 ? 'positive' : 'negative');
    }

    if (feesEl) {
        feesEl.textContent = `+${accumulatedFees.toFixed(2)}`;
        feesEl.className = 'value positive';
    }
}

// Ensure init
init();
