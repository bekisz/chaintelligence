const DEFAULT_ADDRESS = '0x78d6f68df933995aa1b6840eacfa12e4759b0e13';
let targetAddress = localStorage.getItem('lp_target_address') || DEFAULT_ADDRESS;
// Hosted Service (Deprecated for Mainnet) -> 'https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3'
// Decentralized Network -> 'https://gateway.thegraph.com/api/[API_KEY]/subgraphs/id/5zvR82QoaXYFyQB52949LAXkzExk58zE44gQwFv7wJ5q'

// We will default to a placeholder and ask user to input key if it fails.
let apiKey = localStorage.getItem('graph_api_key') || '';
// Uniswap V3 Subgraph ID on The Graph Network
const SUBGRAPH_ID = '5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV';

const NETWORKS = {
    'mainnet': {
        name: 'Ethereum',
        v3: ['5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV'],
        v4: [
            'DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G' // Confirmed working V4 subgraph
        ]
    },
    'arbitrum': {
        name: 'Arbitrum',
        v3: [], // IDs were invalid/not found
        v4: []  // IDs were invalid/not found
    },
    'base': {
        name: 'Base', // IDs were invalid/not found
        v3: [],
        v4: []
    }
};

function getSubgraphUrl(subgraphId) {
    if (!apiKey || !subgraphId) return null;
    return `https://gateway.thegraph.com/api/${apiKey}/subgraphs/id/${subgraphId}`;
}

async function fetchNetworkData(networkKey, addresses) {
    const network = NETWORKS[networkKey];
    const data = { allPositions: [], allBaselines: {}, ethPriceUSD: 0 };

    // Group fetch tasks for v3 and v4
    const tasks = [];
    if (network.v3) network.v3.forEach(id => tasks.push(fetchProtocolData(id, addresses, 'v3', network.name)));
    if (network.v4) network.v4.forEach(id => tasks.push(fetchProtocolData(id, addresses, 'v4', network.name)));

    const results = await Promise.all(tasks);
    results.forEach(res => {
        if (res && res.positions) {
            data.allPositions.push(...res.positions);
            Object.assign(data.allBaselines, res.baselines);
            data.ethPriceUSD = Math.max(data.ethPriceUSD, res.ethPriceUSD);
        }
    });

    return data;
}

async function fetchProtocolData(subgraphId, addresses, version, networkName) {
    const url = getSubgraphUrl(subgraphId);
    if (!url) {
        console.warn(`[WARN] Skipping ${networkName} ${version} (ID: ${subgraphId}) due to missing API key or subgraph ID.`);
        return null;
    }

    const now = Math.floor(Date.now() / 1000);

    // Addresses array for query construction
    const addressesString = addresses.map(a => `\\"${a.toLowerCase()}\\"`).join(',');

    let query;

    if (version === 'v4') {
        // V4: Event-based (ModifyLiquidity)
        // Corrected Schema based on verification:
        // Pool has token0, token1, token0Price (no currency0/1 or price)
        query = `
        {
            modifyLiquidities(
                where: { origin_in: [${addressesString}] },
                first: 1000,
                orderBy: timestamp,
                orderDirection: asc
            ) {
                id
                transaction { id }
                timestamp
                pool {
                    id
                    token0 { symbol decimals derivedETH }
                    token1 { symbol decimals derivedETH }
                    feeTier
                    tick
                    token0Price
                    token1Price
                }
                tickLower
                tickUpper
                amount
            }
            bundle(id: "1") {
                ethPriceUSD
            }
        }
        `;
    } else {
        // V3: Position Entity-based
        query = `
        {
            bundle(id: "1") { ethPriceUSD }
            positions(where: { owner_in: [${addressesString}], liquidity_gt: 0 }) {
                id
                liquidity
                tickLower { tickIdx feeGrowthOutside0X128 feeGrowthOutside1X128 }
                tickUpper { tickIdx feeGrowthOutside0X128 feeGrowthOutside1X128 }
                feeGrowthInside0LastX128
                feeGrowthInside1LastX128
                collectedFeesToken0
                collectedFeesToken1
                token0 { symbol decimals derivedETH }
                token1 { symbol decimals derivedETH }
                pool {
                    id
                    feeTier
                    tick
                    token0Price
                    token1Price
                    feeGrowthGlobal0X128
                    feeGrowthGlobal1X128
                }
            }
            positionSnapshots(
                where: { owner_in: [${addressesString}] },
                orderBy: timestamp, orderDirection: desc, first: 100
            ) {
                id position { id } timestamp feeGrowthInside0LastX128 feeGrowthInside1LastX128 collectedFeesToken0 collectedFeesToken1
            }
        }
        `;
    }

    try {
        console.log(`[FETCH] Querying ${networkName} ${version}...`);
        const response = await fetch(url, { method: 'POST', body: JSON.stringify({ query }) });
        const json = await response.json();

        if (json.errors) {
            console.warn(`[WARN] GraphQL errors for ${networkName} ${version}:`, json.errors);
            return null;
        }

        const ethPriceUSD = parseFloat(json.data.bundle?.ethPriceUSD || 0);
        let positions = [];
        const baselines = {};

        if (version === 'v4') {
            // === V4 Reconstruction Logic ===
            const events = json.data.modifyLiquidities || [];

            // Group by identifier: pool + ticks
            const positionsMap = {};

            events.forEach(evt => {
                const poolId = evt.pool.id;
                const tL = evt.tickLower;
                const tU = evt.tickUpper;
                // Create a synthetic ID for the position
                const posKey = `${poolId}-${tL}-${tU}-${evt.pool.feeTier}`;

                if (!positionsMap[posKey]) {
                    // Initialize if new
                    positionsMap[posKey] = {
                        id: posKey,
                        network: networkName,
                        version: 'v4',
                        // Map V4 structure to V3 expectation
                        pool: {
                            id: evt.pool.id,
                            feeTier: evt.pool.feeTier,
                            tick: parseInt(evt.pool.tick || 0),
                            token0Price: evt.pool.token0Price || "0",
                            token1Price: evt.pool.token1Price || "0",
                            // Missing global fee growth data in events
                            feeGrowthGlobal0X128: "0",
                            feeGrowthGlobal1X128: "0"
                        },
                        token0: evt.pool.token0 || { symbol: '???', decimals: 18, derivedETH: 0 },
                        token1: evt.pool.token1 || { symbol: '???', decimals: 18, derivedETH: 0 },
                        tickLower: { tickIdx: parseInt(tL), feeGrowthOutside0X128: "0", feeGrowthOutside1X128: "0" },
                        tickUpper: { tickIdx: parseInt(tU), feeGrowthOutside0X128: "0", feeGrowthOutside1X128: "0" },
                        liquidity: BigInt(0),
                        // Fees tracking not available in this reconstruction method yet
                        feeGrowthInside0LastX128: "0",
                        feeGrowthInside1LastX128: "0",
                        collectedFeesToken0: "0",
                        collectedFeesToken1: "0"
                    };
                }

                // Aggregate Liquidity
                if (evt.amount) {
                    positionsMap[posKey].liquidity += BigInt(evt.amount);
                }
            });

            // Filter out closed positions (liquidity <= 0) and formatted
            positions = Object.values(positionsMap)
                .filter(p => p.liquidity > 0n)
                .map(p => {
                    p.liquidity = p.liquidity.toString();
                    p.ethPriceUSD = ethPriceUSD;
                    return p;
                });

            console.log(`[V4] Reconstructed ${positions.length} active positions from ${events.length} events.`);

        } else {
            // === V3 Standard Processing ===
            if (json.data.positions) {
                positions = json.data.positions.map(p => ({
                    ...p,
                    version: 'v3',
                    network: networkName,
                    ethPriceUSD: ethPriceUSD,
                    // Ensure tick objects are parsed if coming from different schema versions
                    tickLower: typeof p.tickLower === 'object' ? p.tickLower : { tickIdx: parseInt(p.tickLower) },
                    tickUpper: typeof p.tickUpper === 'object' ? p.tickUpper : { tickIdx: parseInt(p.tickUpper) }
                }));
            }

            // Snapshot processing (V3 only for now)
            if (json.data.positionSnapshots) {
                const grouped = {};
                json.data.positionSnapshots.forEach(s => {
                    if (!grouped[s.position.id]) grouped[s.position.id] = [];
                    grouped[s.position.id].push(s);
                });

                // Helper to find closest snapshot
                Object.keys(grouped).forEach(posId => {
                    const snaps = grouped[posId];
                    let b24 = null; let d24 = Infinity;
                    let b7 = null; let d7 = Infinity;
                    // Current time is in seconds
                    snaps.forEach(s => {
                        const age = now - s.timestamp;
                        // 24h search (allow 4h buffer to ignore very recent updates if wanted, but standard is find closest to 24h)
                        // Using simplier logic: closest to 24h ago
                        const diff24 = Math.abs(age - 86400);
                        if (age >= 3600 && diff24 < d24) { d24 = diff24; b24 = s; }

                        const diff7 = Math.abs(age - 604800);
                        if (age >= 86400 && diff7 < d7) { d7 = diff7; b7 = s; }
                    });
                    baselines[posId] = { snap24h: b24, snap7d: b7 };
                });
            }
        }

        return { positions, baselines, ethPriceUSD };

    } catch (e) {
        console.error(`Fetch failed for ${networkName} ${version}:`, e);
        return null;
    }
}

async function fetchPositions() {
    const addresses = targetAddress.split(',').map(a => a.trim()).filter(a => a.startsWith('0x'));
    if (addresses.length === 0) throw new Error("No valid Ethereum addresses found.");

    const results = await Promise.all(Object.keys(NETWORKS).map(key => fetchNetworkData(key, addresses)));

    const aggregated = {
        positions: [],
        baselinesByPos: {},
        ethPriceUSD: 0
    };

    results.forEach(res => {
        if (!res) return;
        aggregated.positions.push(...res.allPositions);
        Object.assign(aggregated.baselinesByPos, res.allBaselines);
        if (res.ethPriceUSD > aggregated.ethPriceUSD) aggregated.ethPriceUSD = res.ethPriceUSD;
    });

    return aggregated;
}

function tickToPrice(tick) {
    return Math.pow(1.0001, tick);
}

function getSqrtRatioAtTick(tick) {
    return Math.sqrt(Math.pow(1.0001, tick));
}

// Calculate amounts based on liquidity and range
// Formulas derived from Uniswap V3 Whitepaper
function getAmountsForLiquidity(liquidity, currentTick, tickLower, tickUpper, dec0, dec1) {
    const L = parseFloat(liquidity);
    if (isNaN(L)) {
        console.error("L is NaN");
        return { amount0: NaN, amount1: NaN };
    }

    const sa = getSqrtRatioAtTick(tickLower);
    const sb = getSqrtRatioAtTick(tickUpper);
    let sp = getSqrtRatioAtTick(currentTick);

    if (isNaN(sa) || isNaN(sb) || isNaN(sp)) {
        // console.error("SqrtRatio NaN:", { sa, sb, sp, currentTick, tickLower, tickUpper });
        return { amount0: NaN, amount1: NaN };
    }

    // Clamp price if out of range
    if (currentTick < tickLower) sp = sa;
    if (currentTick > tickUpper) sp = sb;

    let amount0 = 0;
    let amount1 = 0;

    // Range Logic
    if (currentTick < tickLower) {
        amount0 = L * (sb - sa) / (sa * sb);
        amount1 = 0;
    } else if (currentTick >= tickUpper) {
        amount0 = 0;
        amount1 = L * (sb - sa);
    } else {
        amount0 = L * (sb - sp) / (sp * sb);
        amount1 = L * (sp - sa);
    }

    const adj0 = amount0 / Math.pow(10, dec0);
    const adj1 = amount1 / Math.pow(10, dec1);

    return { amount0: adj0, amount1: adj1 };
}

// Fee Calculation Logic
// All inputs are strings representing large integers (X128 format)
function getUncollectedFees(
    liquidity,
    currentTick,
    tickLower,
    tickUpper,
    feeGrowthGlobal0X128,
    feeGrowthGlobal1X128,
    feeGrowthOutside0X128Lower,
    feeGrowthOutside1X128Lower,
    feeGrowthOutside0X128Upper,
    feeGrowthOutside1X128Upper,
    feeGrowthInside0LastX128,
    feeGrowthInside1LastX128,
    dec0, dec1
) {
    console.log('[FEE] Calculating fees - has data:', !!feeGrowthGlobal0X128, !!feeGrowthOutside0X128Lower);
    // If any required field is missing (e.g. older subgraphs), return 0
    if (!feeGrowthGlobal0X128 || !feeGrowthOutside0X128Lower) return { fee0: 0, fee1: 0 };

    const Q128 = BigInt("340282366920938463463374607431768211456"); // 2^128

    const global0 = BigInt(feeGrowthGlobal0X128);
    const global1 = BigInt(feeGrowthGlobal1X128);

    // Calculate Fee Growth Below Lower Tick
    // If currentTick >= tickLower, fees below are just what's outside (initialized below)
    // If currentTick < tickLower, fees below assumption flips: global - outside
    let belowGrowth0, belowGrowth1;
    const lower0 = BigInt(feeGrowthOutside0X128Lower);
    const lower1 = BigInt(feeGrowthOutside1X128Lower);

    if (currentTick >= tickLower) {
        belowGrowth0 = lower0;
        belowGrowth1 = lower1;
    } else {
        belowGrowth0 = global0 - lower0;
        belowGrowth1 = global1 - lower1;
    }

    // Calculate Fee Growth Above Upper Tick
    // If currentTick < tickUpper, fees above are just outside
    // If currentTick >= tickUpper, fees above flip: global - outside
    let aboveGrowth0, aboveGrowth1;
    const upper0 = BigInt(feeGrowthOutside0X128Upper);
    const upper1 = BigInt(feeGrowthOutside1X128Upper);

    if (currentTick < tickUpper) {
        aboveGrowth0 = upper0;
        aboveGrowth1 = upper1;
    } else {
        aboveGrowth0 = global0 - upper0;
        aboveGrowth1 = global1 - upper1;
    }

    // Fee Growth Inside
    const insideGrowth0 = global0 - belowGrowth0 - aboveGrowth0;
    const insideGrowth1 = global1 - belowGrowth1 - aboveGrowth1;

    // Uncollected Fees = Liquidity * (FeeGrowthInside - FeeGrowthInsideLast)
    // IMPORTANT: Subgraph feeGrowthInsideLastX128 is "fee growth inside at last update"
    // But calculate it carefully.

    const insideLast0 = BigInt(feeGrowthInside0LastX128);
    const insideLast1 = BigInt(feeGrowthInside1LastX128);

    let uncollected0_X128 = (insideGrowth0 - insideLast0) * BigInt(liquidity);
    let uncollected1_X128 = (insideGrowth1 - insideLast1) * BigInt(liquidity);

    // Filter negative (shouldn't happen mathematically unless data lag/wrap)
    if (uncollected0_X128 < 0n) uncollected0_X128 = 0n;
    if (uncollected1_X128 < 0n) uncollected1_X128 = 0n;

    // Divide by Q128 to get raw token amount
    const fee0Raw = Number(uncollected0_X128 * 10000n / Q128) / 10000; // retain some precision before div
    const fee1Raw = Number(uncollected1_X128 * 10000n / Q128) / 10000;

    // Adjust decimals
    const fee0 = fee0Raw / Math.pow(10, dec0);
    const fee1 = fee1Raw / Math.pow(10, dec1);

    console.log('[FEE] Results:', { fee0Raw, fee1Raw, fee0, fee1, dec0, dec1 });

    return { fee0, fee1 };
}

function formatCurrency(val) {
    if (val === undefined || isNaN(val)) return 'NaN';
    if (!isFinite(val)) return '∞';
    if (val < 0.000001) return val.toExponential(4);
    if (val < 0.001) return val.toExponential(2);
    if (val < 1) return val.toFixed(4);
    if (val < 100) return val.toFixed(2);
    return val.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatCurrencyUSD(val) {
    if (val === undefined || isNaN(val)) return '-';
    return val.toLocaleString(undefined, { style: 'currency', currency: 'USD' });
}

function calculateAccrual(pos, snapshot, currentFees, p0_eth, p1_eth, ethPriceUSD, totalValueUSD, windowLabel) {
    if (!snapshot) return null;

    const nowMs = Date.now();
    const snapshotMs = snapshot.timestamp * 1000;
    const daysSince = (nowMs - snapshotMs) / (1000 * 60 * 60 * 24);

    // Filter: If we want a 7d average but the snapshot is < 2 days old, it's not a reliable "7d" measure
    if (windowLabel === '7d' && daysSince < 2) return null;
    if (daysSince <= 0) return null;

    // Formula: Accrued = (Current_Collected - Historical_Collected) + Current_Unclaimed
    // We use the collected fees from the snapshot provided (which could be 24h or 7d old)
    const collected0Delta = parseFloat(pos.collectedFeesToken0 || 0) - parseFloat(snapshot.collectedFeesToken0 || 0);
    const collected1Delta = parseFloat(pos.collectedFeesToken1 || 0) - parseFloat(snapshot.collectedFeesToken1 || 0);

    const totalAccrued0 = Math.max(0, collected0Delta + currentFees.fee0);
    const totalAccrued1 = Math.max(0, collected1Delta + currentFees.fee1);

    const dailyAccrued0 = totalAccrued0 / daysSince;
    const dailyAccrued1 = totalAccrued1 / daysSince;

    const dailyUSD = (dailyAccrued0 * p0_eth + dailyAccrued1 * p1_eth) * ethPriceUSD;
    const projectedApr = totalValueUSD > 0 ? (dailyUSD * 365 / totalValueUSD) * 100 : 0;

    return {
        dailyAccrued0,
        dailyAccrued1,
        dailyUSD,
        projectedApr,
        daysSince,
        snapshotDate: new Date(snapshotMs).toLocaleDateString()
    };
}

function renderPositions(positions, ethPriceUSD, baselinesByPos = {}) {
    const grid = document.getElementById('dashboard-grid');
    grid.innerHTML = '';

    if (!positions || positions.length === 0) {
        grid.innerHTML = '<div class="error-message">No active positions found for the provided addresses.</div>';
        return;
    }

    // First process all positions to calculate values and enable sorting
    const processedPositions = positions.map(pos => {
        const t0 = pos.token0?.symbol || '???';
        const t1 = pos.token1?.symbol || '???';
        const fee = (pos.pool?.feeTier || 0) / 10000;
        const net = pos.network || 'Ethereum';

        const dec0 = parseInt(pos.token0?.decimals) || 18;
        const dec1 = parseInt(pos.token1?.decimals) || 18;
        const tickLower = parseInt(pos.tickLower?.tickIdx ?? pos.tickLower);
        const tickUpper = parseInt(pos.tickUpper?.tickIdx ?? pos.tickUpper);
        let currentTick = parseInt(pos.pool.tick);

        if (isNaN(currentTick) && pos.pool.token1Price) {
            const price = parseFloat(pos.pool.token1Price);
            const rawPrice = price / Math.pow(10, dec0 - dec1);
            currentTick = Math.floor(Math.log(rawPrice) / Math.log(1.0001));
        }

        const priceLow = tickToPrice(tickLower) * Math.pow(10, dec0 - dec1);
        const priceHigh = tickToPrice(tickUpper) * Math.pow(10, dec0 - dec1);
        const currentPrice = tickToPrice(currentTick) * Math.pow(10, dec0 - dec1);

        const amounts = getAmountsForLiquidity(pos.liquidity, currentTick, tickLower, tickUpper, dec0, dec1);
        const fees = getUncollectedFees(
            pos.liquidity, currentTick, tickLower, tickUpper,
            pos.pool.feeGrowthGlobal0X128, pos.pool.feeGrowthGlobal1X128,
            pos.tickLower?.feeGrowthOutside0X128, pos.tickLower?.feeGrowthOutside1X128,
            pos.tickUpper?.feeGrowthOutside0X128, pos.tickUpper?.feeGrowthOutside1X128,
            pos.feeGrowthInside0LastX128, pos.feeGrowthInside1LastX128,
            dec0, dec1
        );

        const p0_eth = parseFloat(pos.token0.derivedETH || 0);
        const p1_eth = parseFloat(pos.token1.derivedETH || 0);
        const totalValueUSD = ((amounts.amount0 || 0) * p0_eth + (amounts.amount1 || 0) * p1_eth) * ethPriceUSD;
        const feesValueUSD = (fees.fee0 * p0_eth + fees.fee1 * p1_eth) * ethPriceUSD;

        const baselines = baselinesByPos[pos.id] || {};
        const metrics24h = calculateAccrual(pos, baselines.snap24h, fees, p0_eth, p1_eth, ethPriceUSD, totalValueUSD, "24h");
        const metrics7d = calculateAccrual(pos, baselines.snap7d, fees, p0_eth, p1_eth, ethPriceUSD, totalValueUSD, "7d");

        const inRange = currentTick >= tickLower && currentTick <= tickUpper;

        return {
            pos, t0, t1, fee, net, amounts, fees, totalValueUSD, feesValueUSD,
            metrics24h, metrics7d, inRange, priceLow, priceHigh, currentPrice
        };
    });

    // Sort by Total Value USD Descending
    processedPositions.sort((a, b) => b.totalValueUSD - a.totalValueUSD);

    processedPositions.forEach(item => {
        const { pos, t0, t1, fee, net, amounts, fees, totalValueUSD, feesValueUSD, metrics24h, metrics7d, inRange, priceLow, priceHigh, currentPrice } = item;
        const ver = pos.version || 'v3';

        const row = document.createElement('div');
        row.className = 'position-card';
        row.innerHTML = `
            <div class="card-header">
                <div class="pair-name" style="font-size: 1.1rem">${t0}/${t1} <span class="fee-tier">${fee}%</span></div>
                <div style="font-size: 0.75rem; color: #60a5fa; font-weight: 600; margin-top: 0.2rem;">${net} <span style="opacity: 0.6">(${ver})</span></div>
                <div class="range-status ${inRange ? 'status-in-range' : 'status-out-range'}" style="margin-top: 0.5rem">
                    ${inRange ? 'IN RANGE' : 'OUT RANGE'}
                </div>
            </div>

            <div>
                <div class="metric-label">Value & Assets</div>
                <div class="metric-value" style="color: #22c55e">${formatCurrencyUSD(totalValueUSD)}</div>
                <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 0.25rem;">
                    ${formatCurrency(amounts.amount0)} ${t0}<br>
                    ${formatCurrency(amounts.amount1)} ${t1}
                </div>
            </div>

            <div style="background: rgba(59, 130, 246, 0.05); padding: 0.5rem; border-radius: 8px;">
                <div class="metric-label">Unclaimed Fees</div>
                <div class="metric-value" style="color: #60a5fa">${formatCurrencyUSD(feesValueUSD)}</div>
                <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 0.25rem;">
                    ${formatCurrency(fees.fee0)} ${t0}<br>
                    ${formatCurrency(fees.fee1)} ${t1}
                </div>
            </div>

            ${metrics24h ? `
            <div>
                <div class="metric-label">24h Est. Accrual</div>
                <div class="metric-value">${formatCurrencyUSD(metrics24h.dailyUSD)}</div>
                <div style="color: var(--accent-green); font-weight: 600; font-size: 0.85rem; margin-top: 0.15rem;">
                    ${metrics24h.projectedApr.toFixed(1)}% APR
                </div>
                <div style="font-size: 0.6rem; color: #64748b;">v. ${metrics24h.snapshotDate}</div>
            </div>
            ` : '<div>-</div>'}

            ${metrics7d ? `
            <div>
                <div class="metric-label">7d Avg. Accrual</div>
                <div class="metric-value">${formatCurrencyUSD(metrics7d.dailyUSD)}</div>
                <div style="color: var(--accent-green); font-weight: 600; font-size: 0.85rem; margin-top: 0.15rem;">
                    ${metrics7d.projectedApr.toFixed(1)}% APR
                </div>
                <div style="font-size: 0.6rem; color: #64748b;">v. ${metrics7d.snapshotDate}</div>
            </div>
            ` : '<div>-</div>'}

            <div class="price-range-display">
                <div class="range-row">
                    <span>${formatCurrency(priceLow)}</span>
                    <span style="opacity: 0.5">-</span>
                    <span>${formatCurrency(priceHigh)}</span>
                </div>
                <div class="current-price-indicator">
                    Now: <strong>${formatCurrency(currentPrice)}</strong>
                </div>
            </div>
        `;
        grid.appendChild(row);
    });
}

async function init() {
    // UI Setup
    const keyInput = document.getElementById('api-key-input');
    const saveBtn = document.getElementById('save-key-btn');

    if (apiKey) {
        keyInput.value = apiKey;
    }

    const addressInput = document.getElementById('address-input');
    const saveAddressBtn = document.getElementById('save-address-btn');

    if (targetAddress) {
        addressInput.value = targetAddress;
    }

    if (saveAddressBtn) {
        saveAddressBtn.addEventListener('click', () => {
            const val = addressInput.value.trim().toLowerCase();
            if (val) {
                // Basic validation: at least one token looks like 0x...
                const parts = val.split(',').map(p => p.trim());
                if (parts.some(p => p.startsWith('0x'))) {
                    localStorage.setItem('lp_target_address', val);
                    targetAddress = val;
                    alert('Addresses Saved. Refreshing...');
                    location.reload();
                } else {
                    alert('Please enter at least one valid Ethereum address starting with 0x');
                }
            }
        });
    }

    if (saveBtn) {
        saveBtn.addEventListener('click', () => {
            const val = keyInput.value.trim();
            if (val) {
                localStorage.setItem('graph_api_key', val);
                apiKey = val;
                alert('API Key Saved. Refreshing...');
                location.reload();
            }
        });
    }

    try {
        const data = await fetchPositions();
        renderPositions(data.positions, data.ethPriceUSD, data.baselinesByPos);
    } catch (err) {
        document.getElementById('dashboard-grid').innerHTML = `
            <div class="error-message">
                <strong>Error:</strong> ${err.message}<br><br>
                <em>Note: The Graph Hosted Service is deprecated. You must provide a valid API Key.</em>
            </div>
        `;
    }
}

document.addEventListener('DOMContentLoaded', init);
