// Browser/Node compatibility
let _CONFIG_INTERNAL;
if (typeof require !== 'undefined') {
    _CONFIG_INTERNAL = require('./config.js');
} else if (typeof CONFIG !== 'undefined') {
    _CONFIG_INTERNAL = CONFIG;
} else if (typeof window !== 'undefined' && window.CONFIG) {
    _CONFIG_INTERNAL = window.CONFIG;
} else {
    _CONFIG_INTERNAL = {};
}
/**
 * Represents a Financial Asset (Token).
 */
class Asset {
    /**
     * @param {string} id Unique identifier for the asset (e.g., 'ethereum')
     * @param {string} symbol Symbol of the asset (e.g., 'eth')
     * @param {string} name Name of the asset (e.g., 'Ethereum')
     */
    #symbol = "";
    #priceData = null;
    #isHourlyPriceData = false;
    constructor(symbol) {
        this.#symbol = symbol;
        this.#priceData = null; // Lazy-initialized cache of [timestamp, price]
        this.#isHourlyPriceData = false;
    }
    get symbol() {
        return this.#symbol;
    }
    get priceData() {
        return this.#priceData;
    }
    get isHourlyPriceData() {
        return this.#isHourlyPriceData;
    }
    getFirstPriceDate() {
        if (!this.priceData) {
            // Lazy init: fetch daily data as default behavior for getPriceAt
            throw new Error('Price data not initialized. Call Asset.fetchHistory() first.');
        }
        return new Date(this.#priceData[0][0]);
    }
    getLastPriceDate() {
        if (!this.priceData) {
            // Lazy init: fetch daily data as default behavior for getPriceAt
            throw new Error('Price data not initialized. Call Asset.fetchHistory() first.');
        }
        return new Date(this.#priceData[this.#priceData.length - 1][0]);
    }
    /**
     * Look up the price closest to a given JS Date.
     * Uses lazy initialization to fetch data if not already present.
     * 
     * @param {Date} date Date object to look up
     * @returns Closest price found
     */
    getPriceAt(date) {
        if (!this.priceData) {
            // Lazy init: fetch daily data as default behavior for getPriceAt
            throw new Error('Price data not initialized. Call Asset.fetchHistory() first.');
        }

        const data = this.priceData;
        if (!data || data.length === 0) return null;
        if (data.length === 1) return data[0][1];

        const targetTs = date.getTime();
        const firstTs = data[0][0];
        const lastTs = data[data.length - 1][0];
        const oneDayMs = 24 * 60 * 60 * 1000;

        if (targetTs < firstTs - oneDayMs || targetTs > lastTs + oneDayMs) {
            throw new Error(`Requested date ${date.toISOString()} is out of range [${new Date(firstTs).toISOString()} - ${new Date(lastTs).toISOString()}] by more than 1 day.`);
        }

        if (targetTs <= firstTs) return data[0][1];
        if (targetTs >= lastTs) return data[data.length - 1][1];

        // Direct index calculation leveraging even distribution (O(1) lookup)
        const interval = data[1][0] - data[0][0];
        const index = Math.round((targetTs - firstTs) / interval);
        const clampedIndex = Math.max(0, Math.min(data.length - 1, index));
        // console.log(`  getPriceAt(${date.toISOString()}) = ${data[clampedIndex][1]} , approximated with TS :  (${new Date(data[clampedIndex][0]).toISOString()})`);
        return data[clampedIndex][1];
    }
    /**
     * Fetches historical price data and caches it.
     */
    async fetchHistory(useHourly = false, startTime = 0, endTime = Date.now()) {
        const apiKey = _CONFIG_INTERNAL.CRYPTOCOMPARE_API_KEY;
        if (!useHourly) {
            // Daily: fetch all data at once
            const url = `https://min-api.cryptocompare.com/data/v2/histoday?fsym=${this.symbol}&tsym=USD&allData=true&api_key=${apiKey}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`API Error ${res.status}`);
            const json = await res.json();
            if (json.Response === 'Error') throw new Error(json.Message);
            this.#priceData = json.Data.Data.map(d => [d.time * 1000, d.close]);
            // return this.priceData;
        } else {
            // Hourly: Paginate backwards from endTime until startTime
            let allPoints = [];
            let toTs = Math.floor(endTime / 1000);
            const limit = 2000;
            const startTs = Math.floor(startTime / 1000);
            let fetches = 0;
            const MAX_FETCHES = 50; // Safety limit (~4-5 years)

            while (fetches < MAX_FETCHES) {
                const url = `https://min-api.cryptocompare.com/data/v2/histohour?fsym=${this.symbol}&tsym=USD&limit=${limit}&toTs=${toTs}&api_key=${apiKey}`;
                const res = await fetch(url);
                if (!res.ok) throw new Error(`API Error ${res.status}`);
                const json = await res.json();
                if (json.Response === 'Error') throw new Error(json.Message);

                const data = json.Data.Data;
                if (!data || data.length === 0) break;

                // Map to our format [ms, price]
                const chunk = data.map(d => [d.time * 1000, d.close]);
                allPoints = [...chunk, ...allPoints];

                const earliestTime = data[0].time;
                if (earliestTime <= startTs) break; // Reached start date

                toTs = earliestTime - 3600; // Move before the earliest point
                fetches++;

                // Rate limit civility
                await new Promise(r => setTimeout(r, 100));
            }
            this.#priceData = allPoints;
            // return this.#priceData;
        }
    }

    /**
     * Backward compatibility instance method (Private).
     */
    async #getPriceHistory(useHourly, startTime, endTime) {
        return this.fetchHistory(useHourly, startTime, endTime);
    }
}

/**
 * Represents a Liquidity Pool with metadata.
 */
class LiquidityPool {
    #assetX = null;
    #assetY = null;
    #referenceApr = 0;

    /**
     * @param {Asset} assetX Base Asset
     * @param {Asset} assetY Quote Asset
     * @param {number} referenceApr Base APR for the reference range
     */
    constructor(assetX, assetY, referenceApr) {
        if (!(assetX instanceof Asset) || !(assetY instanceof Asset)) {
            throw new Error('LiquidityPool requires instances of Asset for assetX and assetY');
        }
        this.#assetX = assetX;
        this.#assetY = assetY;
        this.#referenceApr = referenceApr;
    }

    get assetX() { return this.#assetX; }
    get assetY() { return this.#assetY; }
    get referenceApr() { return this.#referenceApr; }

    /**
     * Factory method to create an LpPosition instance from a target capital amount.
     * Calculates the required liquidity (L) based on the current price and boundaries.
     * 
     * @param {number} P_min Lower price boundary
     * @param {number} P Current price of the base asset in quote asset terms
     * @param {number} P_max Upper price boundary
     * @param {number} capital Total capital to deploy in quote asset terms
     * @returns {LiquidityPoolPosition} A new LiquidityPoolPosition instance
     */
    createPositionFromCapital(P_min, P, P_max, capital) {
        const { x: asset1PerLiquidity, y: asset2PerLiquidity } = LiquidityPoolPosition.getAssetsPerLiquidity(P_min, P, P_max);
        const valuePerLiquidity = asset1PerLiquidity * P + asset2PerLiquidity;
        const L = capital / valuePerLiquidity;
        return new LiquidityPoolPosition(P_min, P, P_max, L, this);
    }
}

/**
 * Represents a Uniswap V3 Liquidity Position.
 */
class LiquidityPoolPosition {
    /**
     * @param {number} P_min Lower price boundary
     * @param {number} P_initial Price when position was opened
     * @param {number} P_max Upper price boundary
     * @param {number} L Liquidity amount
     * @param {LiquidityPool} pool Parent liquidity pool
     */
    #P_min = 0;
    #P_initial = 0;
    #P_max = 0;
    #L = 0;
    #pool = null;
    #initialX = 0;
    #initialY = 0;
    constructor(P_min, P_initial, P_max, L, pool) {
        this.#P_min = P_min;
        this.#P_initial = P_initial;
        this.#P_max = P_max;
        this.#L = L;
        this.#pool = pool;

        const { x, y } = this.getTokenAmountsAtPrice(P_initial);
        this.#initialX = x;
        this.#initialY = y;
    }

    get P_min() { return this.#P_min; }
    get P_initial() { return this.#P_initial; }
    get P_max() { return this.#P_max; }
    get L() { return this.#L; }
    get pool() { return this.#pool; }
    get initialX() { return this.#initialX; }
    get initialY() { return this.#initialY; }

    /**
     * Gets the APR from the connected pool.
     */
    get referenceApr() {
        return this.pool.referenceApr;
    }

    /**
     * Calculates the token amounts (x, y) for this position at price P.
     * @param {number} P Current price
     */
    getTokenAmountsAtPrice(P) {
        const { x: x_per_L, y: y_per_L } = LiquidityPoolPosition.getAssetsPerLiquidity(this.P_min, P, this.P_max);
        return { x: x_per_L * this.L, y: y_per_L * this.L };
    }


    /**
     * Computes the token amounts (x, y) per unit of liquidity (L=1) for a V3 position.
     * 
     * @param {number} P_min Lower boundary of the LP range
     * @param {number} P Current price of the base asset in quote asset terms
     * @param {number} P_max Upper boundary of the LP range
     * @returns {object} {x, y} amounts per unit of liquidity
     */
    static getAssetsPerLiquidity(P_min, P, P_max) {
        const sqrtP = Math.sqrt(P);
        const sqrtPa = Math.sqrt(P_min);
        const sqrtPb = Math.sqrt(P_max);
        let asset1PerLiquidity = 0, asset2PerLiquidity = 0;

        if (P < P_min) {
            asset1PerLiquidity = (1 / sqrtPa - 1 / sqrtPb);
        } else if (P > P_max) {
            asset2PerLiquidity = (sqrtPb - sqrtPa);
        } else {
            asset1PerLiquidity = (1 / sqrtP - 1 / sqrtPb);
            asset2PerLiquidity = (sqrtP - sqrtPa);
        }
        return { x: asset1PerLiquidity, y: asset2PerLiquidity };
    }
}

/**
 * Backward compatibility wrapper for getLiquidityAndAmounts.
 */
function getLiquidityAndAmounts(P, P_min, P_max, V_target) {
    const dummyAsset1 = new Asset("T0");
    const dummyAsset2 = new Asset("T1");
    const dummyPool = new LiquidityPool(dummyAsset1, dummyAsset2, 0);
    const pos = dummyPool.createPositionFromCapital(P_min, P, P_max, V_target);
    return { L: pos.L, x: pos.initialX, y: pos.initialY };
}

/**
 * Legacy wrapper for getAssetsPerLiquidity.
 */
function calculateInRangeDurations(priceSeries, minPct, maxPct) {
    if (!priceSeries || priceSeries.length === 0) return [];

    // Determine the average time interval in days between points
    const firstTs = priceSeries[0][0];
    const lastTs = priceSeries[priceSeries.length - 1][0];
    const totalDays = (lastTs - firstTs) / (1000 * 60 * 60 * 24);
    const intervalDays = totalDays / (priceSeries.length - 1) || 1;

    const durations = [];
    for (let i = 0; i < priceSeries.length; i++) {
        const startPrice = priceSeries[i][1];
        const minPrice = startPrice * (1 + minPct);
        const maxPrice = startPrice * (1 + maxPct);
        let count = 0;

        for (let j = i + 1; j < priceSeries.length; j++) {
            const p = priceSeries[j][1];
            if (p >= minPrice && p <= maxPrice) {
                count++;
            } else {
                break;
            }
        }
        // Convert count of points to days
        durations.push(count * intervalDays);
    }
    return durations;
}

/**
 * Calculates Uniswap V3 liquidity L for capital=1 at price=1.
 */
function getLiquidityConcentration(minPct, maxPct) {
    const Pa = 1.0 + minPct;
    const Pb = 1.0 + maxPct;
    // Formula: L = 1 / (2 - sqrt(Pa) - 1/sqrt(Pb))
    return 1 / (2 - Math.sqrt(Pa) - 1 / Math.sqrt(Pb));
}

/**
 * Calculates IL and Daily Return for a given range.
 */
function getRangeMetrics(minPct, maxPct, baseApr) {
    // Normalizing price to 1.0
    const P0 = 1.0;
    const Pa = 1.0 + minPct;
    const Pb = 1.0 + maxPct;

    const sqrtP0 = Math.sqrt(P0);
    const sqrtPa = Math.sqrt(Pa);
    const sqrtPb = Math.sqrt(Pb);

    // Initial assets for L=1
    const x0 = (1 / sqrtP0 - 1 / sqrtPb);
    const y0 = (sqrtP0 - sqrtPa);

    // LP Value at Pa (entirely in asset X)
    const valPa_LP = (1 / sqrtPa - 1 / sqrtPb) * Pa;
    const valPa_HODL = x0 * Pa + y0;
    const lossPa = 1 - (valPa_LP / valPa_HODL);

    // LP Value at Pb (entirely in asset Y)
    const valPb_LP = (sqrtPb - sqrtPa);
    const valPb_HODL = x0 * Pb + y0;
    const lossPb = 1 - (valPb_LP / valPb_HODL);

    const maxLoss = Math.max(lossPa, lossPb);

    // Daily fee return
    const L_std = 1.7071; // Liquidity for -50%/+100% range
    const L_custom = getLiquidityConcentration(minPct, maxPct);
    const multiplier = L_custom / L_std;
    const effectiveApr = baseApr * multiplier;
    const dailyReturn = effectiveApr / 365;

    return { maxLoss, dailyReturn };
}

/**
 * Calculates the number of days needed in-range to cover the maximum IL at boundaries.
 */
function calculateBreakEvenDays(minPct, maxPct, baseApr) {
    const { maxLoss, dailyReturn } = getRangeMetrics(minPct, maxPct, baseApr);
    if (dailyReturn <= 0) return 0;
    return maxLoss / dailyReturn;
}

/**
 * Calculates when a strategy beats the standard -50%/+100% range.
 */
function calculateCompetitiveDays(minPct, maxPct, baseApr) {
    const std = getRangeMetrics(-0.5, 1.0, baseApr);
    const custom = getRangeMetrics(minPct, maxPct, baseApr);

    const diffReturn = custom.dailyReturn - std.dailyReturn;
    const diffLoss = custom.maxLoss - std.maxLoss;

    if (diffReturn <= 0) return Infinity; // Custom range earns less or same, will never beat std or only if IL is less.

    // Competitive Days = (IL_custom - IL_std) / (r_custom - r_std)
    const d = diffLoss / diffReturn;
    return Math.max(0, d);
}

function calculateV3Backtest(priceSeries, minPct, maxPct, rebMinPct, rebMaxPct, baseApr, rebalanceMode, delayDays) {
    const P0_initial = priceSeries[0][1];
    let P0 = P0_initial;
    let P_min = P0 * (1 + minPct);
    let P_max = P0 * (1 + maxPct);
    let P_reb_min = P0 * (1 + rebMinPct);
    let P_reb_max = P0 * (1 + rebMaxPct);

    let currentCapital = 100;
    // We expect Asset instances to be used eventually, for now we can create them from the symbols if known,
    // or pass them into the function. For immediate fix, we create temporary ones if not provided.
    // In actual app.js usage, we should probably pass the Asset objects.
    const asset1 = (typeof baseAsset !== 'undefined') ? baseAsset : new Asset("Asset1");
    const asset2 = (typeof quoteAsset !== 'undefined') ? quoteAsset : new Asset("Asset2");

    const pool = new LiquidityPool(asset1, asset2, baseApr);
    let pos = pool.createPositionFromCapital(P_min, P0, P_max, currentCapital);
    const amt_asset1 = 100 / P0_initial, amt_asset2 = 100;

    const hodlData = [], lpTotalData = [], asset1Data = [], asset2Data = [];
    const minRangeSeries = [], maxRangeSeries = [];
    const minRebSeries = [], maxRebSeries = [];

    // Preserve initial amounts for HODL calculation
    const initialHODL_X = pos.initialX;
    const initialHODL_Y = pos.initialY;

    let accumulatedFees = 0;
    let daysOutOfRange = 0;
    let lastRebalanceTime = priceSeries[0] ? priceSeries[0][0] : 0;

    // Check initial range state
    if (P0 < P_reb_min || P0 > P_reb_max) {
        daysOutOfRange = 0.0001;
    }

    const L_std = getLiquidityConcentration(-0.5, 1.0); // for -50% to +100%
    const L_custom = getLiquidityConcentration(minPct, maxPct);
    const multiplier = L_custom / L_std;

    for (let i = 0; i < priceSeries.length; i++) {
        const [time, P] = priceSeries[i];

        // HODL comparison (from original starting amounts)
        hodlData.push([time, initialHODL_X * P + initialHODL_Y]);
        asset1Data.push([time, amt_asset1 * P]);
        asset2Data.push([time, amt_asset2]);

        // Current position state
        const { x: x_t, y: y_t } = pos.getTokenAmountsAtPrice(P);
        const inRange = (P >= pos.P_min && P <= pos.P_max);
        let val_lp_principal = x_t * P + y_t;

        // 1. Fee Calculation (Accrued daily based on current position)
        if (i > 0) {
            const yearsElapsed = (time - priceSeries[i - 1][0]) / (1000 * 60 * 60 * 24 * 365);
            if (inRange) {
                const effectiveApr = pos.referenceApr * multiplier;
                accumulatedFees += val_lp_principal * effectiveApr * yearsElapsed;
            }
        }

        // Rebalance logic
        let shouldRebalance = false;
        let rebalanceCenterPrice = P;

        if (rebalanceMode === 'periodic') {
            const msSinceLast = time - lastRebalanceTime;
            const requiredMs = (delayDays * 24 * 60 * 60 * 1000) - 3600000;
            if (msSinceLast >= requiredMs) {
                shouldRebalance = true;
                rebalanceCenterPrice = P;
            }
        } else if (rebalanceMode !== 'simple') {
            const timeStepDays = i > 0 ? (time - priceSeries[i - 1][0]) / (1000 * 60 * 60 * 24) : 0;
            if (P < P_reb_min || P > P_reb_max) {
                daysOutOfRange += timeStepDays;
            } else {
                daysOutOfRange = 0;
            }

            if (rebalanceMode === 'time-delayed' && daysOutOfRange >= delayDays) {
                shouldRebalance = true;
            } else if (rebalanceMode === 'settled' && daysOutOfRange >= delayDays) {
                const step = timeStepDays || (1 / 24);
                const requiredPoints = Math.max(1, Math.floor(delayDays / step));
                const window = priceSeries.slice(Math.max(0, i - requiredPoints + 1), i + 1);
                const prices = window.map(p => p[1]);
                const sumLog = prices.reduce((a, b) => a + Math.log(b), 0);
                const geoAvg = Math.exp(sumLog / prices.length);
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

            // Re-instantiate LiquidityPoolPosition
            pos = pool.createPositionFromCapital(P_min, P0, P_max, currentCapital);

            val_lp_principal = currentCapital;
            daysOutOfRange = 0;
            lastRebalanceTime = time;
        }

        lpTotalData.push([time, val_lp_principal + accumulatedFees]);
        minRangeSeries.push((pos.P_min / P0_initial) * 100);
        maxRangeSeries.push((pos.P_max / P0_initial) * 100);
        minRebSeries.push((P_reb_min / P0_initial) * 100);
        maxRebSeries.push((P_reb_max / P0_initial) * 100);
    }

    const inRangeDurations = calculateInRangeDurations(priceSeries, minPct, maxPct);
    const averageInRangeDuration = inRangeDurations.length > 0 ? (inRangeDurations.reduce((a, b) => a + b, 0) / inRangeDurations.length) : 0;

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
        }),
        inRangeDurations,
        averageInRangeDuration,
        breakEvenDays: calculateBreakEvenDays(minPct, maxPct, baseApr),
        competitiveDays: calculateCompetitiveDays(minPct, maxPct, baseApr)
    };
}

// --- Exports for testing and browser ---
if (typeof module !== 'undefined') {
    module.exports = { calculateV3Backtest, getLiquidityAndAmounts, LiquidityPoolPosition, LiquidityPool, Asset, calculateInRangeDurations, calculateBreakEvenDays, calculateCompetitiveDays, getLiquidityConcentration };
}
if (typeof window !== 'undefined') {
    window.calculateV3Backtest = calculateV3Backtest;
    window.getLiquidityAndAmounts = getLiquidityAndAmounts;
    window.LiquidityPoolPosition = LiquidityPoolPosition;
    window.LiquidityPool = LiquidityPool;
    window.Asset = Asset;
    window.calculateInRangeDurations = calculateInRangeDurations;
    window.calculateBreakEvenDays = calculateBreakEvenDays;
    window.calculateCompetitiveDays = calculateCompetitiveDays;
    window.getLiquidityConcentration = getLiquidityConcentration;
}
