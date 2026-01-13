const DEFAULT_ADDRESS = '0x78d6f68df933995aa1b6840eacfa12e4759b0e13';
let targetAddress = localStorage.getItem('lp_target_address') || DEFAULT_ADDRESS;
// Hosted Service (Deprecated for Mainnet) -> 'https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3'
// Decentralized Network -> 'https://gateway.thegraph.com/api/[API_KEY]/subgraphs/id/5zvR82QoaXYFyQB52949LAXkzExk58zE44gQwFv7wJ5q'

// We will default to a placeholder and ask user to input key if it fails.
let apiKey = localStorage.getItem('graph_api_key') || '';
// Uniswap V3 Subgraph ID on The Graph Network
const SUBGRAPH_ID = '5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV';

function getSubgraphUrl() {
    if (!apiKey) {
        // Fallback to hosted service in case it miraculously works or for non-mainnet (but this is mainnet)
        // Or return null to trigger prompt
        return null;
    }
    return `https://gateway.thegraph.com/api/${apiKey}/subgraphs/id/${SUBGRAPH_ID}`;
}

async function fetchPositions() {
    const url = getSubgraphUrl();
    if (!url) {
        throw new Error("Missing Graph API Key. Please enter it above.");
    }

    // Calculate timestamp for 24 hours ago
    const timestamp24hAgo = Math.floor(Date.now() / 1000) - (24 * 60 * 60);

    const query = `
    {
        bundle(id: "1") {
            ethPriceUSD
        }
        positions(where: { owner: "${targetAddress}", liquidity_gt: 0 }) {
            id
            token0 {
                symbol
                decimals
                derivedETH
            }
            token1 {
                symbol
                decimals
                derivedETH
            }
            liquidity
            feeGrowthInside0LastX128
            feeGrowthInside1LastX128
            tickLower {
                tickIdx
                feeGrowthOutside0X128
                feeGrowthOutside1X128
            }
            tickUpper {
                tickIdx
                feeGrowthOutside0X128
                feeGrowthOutside1X128
            }
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
            where: { 
                owner: "${targetAddress}"
            },
            orderBy: timestamp,
            orderDirection: desc,
            first: 100
        ) {
            id
            position {
                id
            }
            timestamp
            feeGrowthInside0LastX128
            feeGrowthInside1LastX128
            collectedFeesToken0
            collectedFeesToken1
        }
    }
    `;

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });

        const json = await response.json();
        if (json.errors) {
            throw new Error(json.errors[0].message);
        }

        // Create a map of position ID to its most recent snapshot from 24h ago
        const snapshotMap = {};
        if (json.data.positionSnapshots) {
            json.data.positionSnapshots.forEach(snap => {
                const posId = snap.position.id;
                // Keep only the most recent snapshot for each position (already ordered desc)
                if (!snapshotMap[posId]) {
                    snapshotMap[posId] = snap;
                }
            });
        }

        return {
            positions: json.data.positions,
            ethPriceUSD: parseFloat(json.data.bundle?.ethPriceUSD || 0),
            snapshots24h: snapshotMap
        };
    } catch (error) {
        console.error("Failed to fetch positions:", error);
        throw error;
    }
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

function renderPositions(positions, ethPriceUSD, snapshots24h = {}) {
    const grid = document.getElementById('dashboard-grid');
    grid.innerHTML = '';

    if (!positions || positions.length === 0) {
        grid.innerHTML = '<div class="error-message">No active positions found for this address.</div>';
        return;
    }

    positions.forEach(pos => {
        const t0 = pos.token0?.symbol || '???';
        const t1 = pos.token1?.symbol || '???';
        const fee = (pos.pool?.feeTier || 0) / 10000;

        let dec0 = parseInt(pos.token0?.decimals);
        let dec1 = parseInt(pos.token1?.decimals);

        if (isNaN(dec0)) { dec0 = 18; }
        if (isNaN(dec1)) { dec1 = 18; }

        // Correctly access nested tickIdx
        let tickLower = parseInt(pos.tickLower?.tickIdx ?? pos.tickLower);
        let tickUpper = parseInt(pos.tickUpper?.tickIdx ?? pos.tickUpper);
        let currentTick = parseInt(pos.pool.tick);

        // Fallback if pool.tick is missing
        if (isNaN(currentTick)) {
            if (pos.pool.token1Price) {
                const price = parseFloat(pos.pool.token1Price);
                const rawPrice = price / Math.pow(10, dec0 - dec1);
                currentTick = Math.floor(Math.log(rawPrice) / Math.log(1.0001));
            }
        }

        // Calculate Prices
        const priceLowRaw = tickToPrice(tickLower);
        const priceHighRaw = tickToPrice(tickUpper);
        const currentPriceRaw = tickToPrice(currentTick);

        const decimalAdjustment = Math.pow(10, dec0 - dec1);

        const priceLow = priceLowRaw * decimalAdjustment;
        const priceHigh = priceHighRaw * decimalAdjustment;
        const currentPrice = currentPriceRaw * decimalAdjustment;

        // Calculate Amounts
        const amounts = getAmountsForLiquidity(pos.liquidity, currentTick, tickLower, tickUpper, dec0, dec1);

        // Calculate Uncollected Fees
        const fees = getUncollectedFees(
            pos.liquidity,
            currentTick,
            tickLower,
            tickUpper,
            pos.pool.feeGrowthGlobal0X128,
            pos.pool.feeGrowthGlobal1X128,
            pos.tickLower?.feeGrowthOutside0X128,
            pos.tickLower?.feeGrowthOutside1X128,
            pos.tickUpper?.feeGrowthOutside0X128,
            pos.tickUpper?.feeGrowthOutside1X128,
            pos.feeGrowthInside0LastX128,
            pos.feeGrowthInside1LastX128,
            dec0, dec1
        );

        // Calculate Fees since snapshot (representing "last action" or historical point)
        let feesSinceSnapshot = { fee0: 0, fee1: 0 };
        let hasSnapshotData = false;
        const snapshot = snapshots24h[pos.id];

        if (snapshot && snapshot.feeGrowthInside0LastX128) {
            hasSnapshotData = true;
            feesSinceSnapshot = getUncollectedFees(
                pos.liquidity,
                currentTick,
                tickLower,
                tickUpper,
                pos.pool.feeGrowthGlobal0X128,
                pos.pool.feeGrowthGlobal1X128,
                pos.tickLower?.feeGrowthOutside0X128,
                pos.tickLower?.feeGrowthOutside1X128,
                pos.tickUpper?.feeGrowthOutside0X128,
                pos.tickUpper?.feeGrowthOutside1X128,
                snapshot.feeGrowthInside0LastX128,
                snapshot.feeGrowthInside1LastX128,
                dec0, dec1
            );
        }

        // Calculate USD Values
        // Value = (amt0 * price0_usd) + (amt1 * price1_usd)
        let totalValueUSD = 0;
        let feesValueUSD = 0;
        let feesSinceSnapshotUSD = 0;

        const p0_eth = parseFloat(pos.token0.derivedETH || 0);
        const p1_eth = parseFloat(pos.token1.derivedETH || 0);

        if (!isNaN(amounts.amount0) && !isNaN(amounts.amount1)) {
            const v0 = amounts.amount0 * p0_eth * ethPriceUSD;
            const v1 = amounts.amount1 * p1_eth * ethPriceUSD;
            totalValueUSD = v0 + v1;
        }

        if (!isNaN(fees.fee0) && !isNaN(fees.fee1)) {
            const fv0 = fees.fee0 * p0_eth * ethPriceUSD;
            const fv1 = fees.fee1 * p1_eth * ethPriceUSD;
            feesValueUSD = fv0 + fv1;
        }

        if (hasSnapshotData && !isNaN(feesSinceSnapshot.fee0) && !isNaN(feesSinceSnapshot.fee1)) {
            const fdv0 = feesSinceSnapshot.fee0 * p0_eth * ethPriceUSD;
            const fdv1 = feesSinceSnapshot.fee1 * p1_eth * ethPriceUSD;
            feesSinceSnapshotUSD = fdv0 + fdv1;
        }

        // Check Range
        const inRange = currentTick >= tickLower && currentTick <= tickUpper;

        const card = document.createElement('div');
        card.className = 'position-card';
        card.innerHTML = `
            <div class="range-status ${inRange ? 'status-in-range' : 'status-out-range'}">
                ${inRange ? 'In Range' : 'Out of Range'}
            </div>
            <div class="card-header">
                <div class="pair-name">
                    ${t0} / ${t1}
                </div>
                <div class="fee-tier">${fee}%</div>
            </div>

            <div class="card-metric" style="border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 1rem; margin-bottom: 1rem;">
                <div class="metric-label">Approx. Value</div>
                <div class="metric-value" style="color: #22c55e; font-size: 1.5rem;">${formatCurrencyUSD(totalValueUSD)}</div>
            </div>

            <div class="card-metric" style="background: rgba(255,255,255,0.05); padding: 0.5rem; border-radius: 8px; margin-bottom: 0.5rem;">
                <div class="metric-label">Estimated Assets</div>
                <div style="display: flex; justify-content: space-between;">
                    <span>${formatCurrency(amounts.amount0)} <strong>${t0}</strong></span>
                    <span>${formatCurrency(amounts.amount1)} <strong>${t1}</strong></span>
                </div>
            </div>

            <div class="card-metric" style="background: rgba(59, 130, 246, 0.1); padding: 0.5rem; border-radius: 8px; margin-bottom: ${hasSnapshotData ? '0.5rem' : '1rem'};">
                <div class="metric-label" style="display:flex; justify-content:space-between">
                    <span>Unclaimed Fees</span>
                    <span style="color: #60a5fa">${formatCurrencyUSD(feesValueUSD)}</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.9em; color: #cbd5e1;">
                    <span>${formatCurrency(fees.fee0)} <strong>${t0}</strong></span>
                    <span>${formatCurrency(fees.fee1)} <strong>${t1}</strong></span>
                </div>
            </div>

            ${hasSnapshotData ? `
            <div class="card-metric" style="background: rgba(34, 197, 94, 0.1); padding: 0.5rem; border-radius: 8px; margin-bottom: 0.5rem;">
                <div class="metric-label" style="display:flex; justify-content:space-between">
                    <span>Fees Since Last Action</span>
                    <span style="color: #22c55e">${formatCurrencyUSD(feesSinceSnapshotUSD)}</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.9em; color: #cbd5e1;">
                    <span>+${formatCurrency(feesSinceSnapshot.fee0)} <strong>${t0}</strong></span>
                    <span>+${formatCurrency(feesSinceSnapshot.fee1)} <strong>${t1}</strong></span>
                </div>
                <div style="font-size: 0.75rem; color: #64748b; margin-top: 0.25rem; text-align: center;">
                    Since ${new Date(snapshot.timestamp * 1000).toLocaleDateString()} ${new Date(snapshot.timestamp * 1000).toLocaleTimeString()}
                </div>
            </div>
            ` : ''}

            ${hasSnapshotData && (parseFloat(snapshot.collectedFeesToken0 || 0) > 0 || parseFloat(snapshot.collectedFeesToken1 || 0) > 0) ? `
            <div class="card-metric" style="background: rgba(168, 85, 247, 0.1); padding: 0.5rem; border-radius: 8px; margin-bottom: 1rem; border: 1px solid rgba(168, 85, 247, 0.3);">
                <div class="metric-label" style="display:flex; justify-content:space-between">
                    <span>Total Fees Collected</span>
                    <span style="color: #a855f7">All-Time</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.9em; color: #cbd5e1; margin-top: 0.25rem;">
                    <span>${formatCurrency(parseFloat(snapshot.collectedFeesToken0))} <strong>${t0}</strong></span>
                    <span>${formatCurrency(parseFloat(snapshot.collectedFeesToken1))} <strong>${t1}</strong></span>
                </div>
            </div>
            ` : (hasSnapshotData ? `
            <div style="margin-bottom: 1rem;"></div>
            ` : '')}

            <div class="price-range-display">
                <div class="range-row">
                    <span class="metric-label">Min Price</span>
                    <span class="metric-value">${formatCurrency(priceLow)}</span>
                </div>
                <div class="range-row">
                    <span class="metric-label">Max Price</span>
                    <span class="metric-value">${formatCurrency(priceHigh)}</span>
                </div>
                 <div class="current-price-indicator">
                    Current: <strong>${formatCurrency(currentPrice)}</strong> ${t1}/${t0}
                </div>
            </div>
            
            <div style="margin-top: 1rem; font-size: 0.75rem; color: #64748b; text-align: center;">
                Liquidity (Raw): ${parseInt(pos.liquidity).toExponential(2)}
            </div>
        `;
        grid.appendChild(card);
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
            if (val && val.startsWith('0x')) {
                localStorage.setItem('lp_target_address', val);
                targetAddress = val;
                alert('Address Saved. Refreshing...');
                location.reload();
            } else {
                alert('Please enter a valid Ethereum address starting with 0x');
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
        renderPositions(data.positions, data.ethPriceUSD, data.snapshots24h);
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
