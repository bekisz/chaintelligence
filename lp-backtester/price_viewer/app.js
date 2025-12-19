const baseSearchInput = document.getElementById('base-search');
const quoteSearchInput = document.getElementById('quote-search');
const baseResults = document.getElementById('base-results');
const quoteResults = document.getElementById('quote-results');
const swapBtn = document.getElementById('swap-btn');
const daysSelect = document.getElementById('days-select');
const loading = document.getElementById('loading');
const errorMessage = document.getElementById('error-message');
const currentPriceEl = document.getElementById('current-price');
const priceChangeEl = document.getElementById('price-change');

let chartInstance = null;
let allCoins = [];

// Defaults
let baseAsset = { id: 'aave', symbol: 'aave', name: 'Aave' };
let quoteAsset = { id: 'usd-coin', symbol: 'usdc', name: 'USDC' };

// --- Initialization ---

async function init() {
    baseSearchInput.value = baseAsset.symbol.toUpperCase();
    quoteSearchInput.value = quoteAsset.symbol.toUpperCase();

    // Fallback: if names differ significantly or we want pretty format
    // But value is user visible text.

    // Load coin list
    await fetchCoinList();

    updateChart();
}

async function fetchCoinList() {
    try {
        const cached = localStorage.getItem('coingecko_top_100');
        const timestamp = localStorage.getItem('coingecko_top_100_ts');
        const now = Date.now();

        // simple cache for 1 day
        if (cached && timestamp && (now - timestamp < 86400000)) {
            allCoins = JSON.parse(cached);
            return;
        }

        // Fetch Top 100 by Market Cap
        const response = await fetch('https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1&sparkline=false');
        if (!response.ok) throw new Error('Failed to fetch coin list');
        allCoins = await response.json();

        // Cache it
        try {
            localStorage.setItem('coingecko_top_100', JSON.stringify(allCoins));
            localStorage.setItem('coingecko_top_100_ts', now);
        } catch (e) { /* ignore quota exceeded */ }

    } catch (error) {
        console.error("Error loading coins:", error);
        // Fallback or alert? The old list might work if cached, but let's just warn.
        alert("Failed to load coin list. Search may not work.");
    }
}

// --- Search Logic ---

function setupSearch(input, resultsContainer, setAssetCallback) {
    input.addEventListener('input', () => {
        const query = input.value.toLowerCase();
        if (query.length < 2) {
            resultsContainer.classList.add('hidden');
            return;
        }

        const matches = allCoins.filter(c =>
            c.symbol.toLowerCase().startsWith(query) ||
            c.name.toLowerCase().includes(query)
        ).slice(0, 50); // limit results

        renderResults(matches, resultsContainer, (coin) => {
            input.value = coin.symbol.toUpperCase();
            resultsContainer.classList.add('hidden');
            setAssetCallback(coin);
            updateChart();
        });
    });

    // Close on click outside
    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !resultsContainer.contains(e.target)) {
            resultsContainer.classList.add('hidden');
        }
    });

    // Show all valid coins if empty? No, too many.
    input.addEventListener('focus', () => {
        if (input.value.length >= 2) {
            input.dispatchEvent(new Event('input')); // trigger search again
        }
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
        div.innerHTML = `
            <span class="symbol">${coin.symbol}</span>
            <span class="name">${coin.name}</span>
        `;
        div.addEventListener('click', () => onSelect(coin));
        container.appendChild(div);
    });

    container.classList.remove('hidden');
}

setupSearch(baseSearchInput, baseResults, (coin) => { baseAsset = coin; });
setupSearch(quoteSearchInput, quoteResults, (coin) => { quoteAsset = coin; });

// --- Swap Logic ---

swapBtn.addEventListener('click', () => {
    // Swap objects
    const temp = baseAsset;
    baseAsset = quoteAsset;
    quoteAsset = temp;

    // Update inputs
    baseSearchInput.value = baseAsset.symbol.toUpperCase();
    quoteSearchInput.value = quoteAsset.symbol.toUpperCase();

    updateChart();
});

daysSelect.addEventListener('change', updateChart);

// --- Chart Data & Rendering ---

async function updateChart() {
    if (!baseAsset || !quoteAsset) return;

    loading.classList.add('active');
    if (errorMessage) {
        errorMessage.classList.add('hidden');
        errorMessage.textContent = '';
    }

    try {
        const days = daysSelect.value;
        const apiKey = CONFIG.CRYPTOCOMPARE_API_KEY;

        // Fetch using Symbol (e.g. BTC) not ID
        // CryptoCompare uses 'fsym' (from symbol) and 'tsym' (to symbol).

        const baseData = await fetchHistory(baseAsset.symbol.toUpperCase(), days, apiKey);
        const quoteData = await fetchHistory(quoteAsset.symbol.toUpperCase(), days, apiKey);

        if (!baseData || !quoteData) throw new Error("Missing data");

        // Calculate Ratio Series
        const ratioSeries = calculateRatioSeries(baseData, quoteData);

        renderChart(ratioSeries, days);
        updateInfo(ratioSeries);

    } catch (error) {
        console.error(error);
        if (errorMessage) {
            errorMessage.textContent = error.message;
            errorMessage.classList.remove('hidden');
        } else {
            alert(`Error: ${error.message}`);
        }
    } finally {
        loading.classList.remove('active');
    }
}

async function fetchHistory(symbol, days, apiKey) {
    // Determine endpoint and limit based on 'days'
    // 1 Day = 1440 minutes (histominute)
    // 7 Days = 168 hours (histohour)
    // 30 Days = 720 hours (histohour) or daily
    // 90 Days = daily
    // 365 Days = daily
    // Max = daily (allData=true)

    let url = 'https://min-api.cryptocompare.com/data/v2/';
    let limit = 2000;

    if (days === '1') {
        url += 'histominute';
        limit = 1440;
    } else if (days === '7' || days === '30') {
        url += 'histohour';
        limit = days === '7' ? 168 : 720;
    } else {
        url += 'histoday';
        // For 365 or specific counts
        limit = days === 'max' ? 2000 : parseInt(days);
    }

    // Build URL
    const allData = days === 'max' ? '&allData=true' : '';
    // Use LIMIT if not allData
    const limitParam = days === 'max' ? '' : `&limit=${limit}`;

    const fullUrl = `${url}?fsym=${symbol}&tsym=USD${limitParam}${allData}&api_key=${apiKey}`;

    const res = await fetch(fullUrl);
    if (!res.ok) {
        throw new Error(`API Error ${res.status}`);
    }
    const json = await res.json();

    if (json.Response === 'Error') {
        throw new Error(json.Message);
    }

    // Map to [[timestamp_ms, price], ...]
    // CryptoCompare returns { time: unix_seconds, close: price, ... }
    return json.Data.Data.map(d => [d.time * 1000, d.close]);
}

function calculateRatioSeries(basePrices, quotePrices) {
    // We need to match timestamps. They might not be identical.
    // CoinGecko hourly data should be roughly aligned.
    // Strategy: For each base point, find matching quote point (nearest time).

    const series = [];

    // Create map for faster lookup if massive, but linear scan for sorted time arrays is O(N+M)
    let qIdx = 0;

    for (let i = 0; i < basePrices.length; i++) {
        const [time, bPrice] = basePrices[i];

        // Find closest point in quotePrices
        // Since both sorted by time, we can advance qIdx
        while (qIdx < quotePrices.length - 1 &&
            Math.abs(quotePrices[qIdx + 1][0] - time) < Math.abs(quotePrices[qIdx][0] - time)) {
            qIdx++;
        }

        const [qTime, qPrice] = quotePrices[qIdx];

        // If time diff is too large (e.g. > 1 hour gap?), optionally skip.
        // For now, lenient.

        if (qPrice !== 0) {
            series.push([time, bPrice / qPrice]);
        }
    }
    return series;
}

function renderChart(series, days) {
    const ctx = document.getElementById('priceChart').getContext('2d');
    const labels = series.map(p => {
        const d = new Date(p[0]);
        return days === '1' ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : d.toLocaleDateString();
    });
    const dataPoints = series.map(p => p[1]);

    // Gradient
    const gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, 'rgba(59, 130, 246, 0.5)');
    gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');

    if (chartInstance) chartInstance.destroy();

    chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: `${baseAsset.symbol.toUpperCase()} / ${quoteAsset.symbol.toUpperCase()}`,
                data: dataPoints,
                borderColor: '#3b82f6',
                backgroundColor: gradient,
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1a1d24',
                    titleColor: '#fff',
                    bodyColor: '#fff',
                    borderColor: '#2d3748',
                    borderWidth: 1,
                    displayColors: false,
                    callbacks: {
                        label: function (ctx) {
                            return ctx.parsed.y.toPrecision(6);
                        }
                    }
                }
            },
            scales: {
                x: { grid: { color: '#2d3748', drawBorder: false }, ticks: { color: '#9ca3af', maxTicksLimit: 8 } },
                y: {
                    grid: { color: '#2d3748', drawBorder: false },
                    ticks: {
                        color: '#9ca3af',
                        callback: (v) => v.toPrecision(4)
                    }
                }
            }
        }
    });
}

function updateInfo(series) {
    if (series.length === 0) return;
    const end = series[series.length - 1][1];
    const start = series[0][1];
    const change = ((end - start) / start) * 100;

    currentPriceEl.textContent = end.toPrecision(6);
    priceChangeEl.textContent = `${change > 0 ? '+' : ''}${change.toFixed(2)}%`;
    priceChangeEl.className = 'value ' + (change >= 0 ? 'positive' : 'negative');
}

// Ensure init
init();
