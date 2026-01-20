// Native fetch is available in Node.js 18+

// Configuration
const API_KEY = process.argv[2];
if (!API_KEY) {
    console.error('❌ Error: Please provide your Graph API Key as the first argument.');
    console.error('   Usage: node lp-backtester/lp-info/test_v4_positions.js <API_KEY> [TARGET_ADDRESS]');
    process.exit(1);
}

const SUBGRAPH_ID = 'DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G';
const SUBGRAPH_URL = `https://gateway.thegraph.com/api/${API_KEY}/subgraphs/id/${SUBGRAPH_ID}`;

// Use a known address or one provided by user (2nd argument)
// Defaulting to the one seen in logs/defaults: 0x78d6f68df933995aa1b6840eacfa12e4759b0e13
const TARGET_ADDRESS = process.argv[3] || '0x78d6f68df933995aa1b6840eacfa12e4759b0e13';

async function testV4Positions() {
    console.log(`\n🔍 Testing Uniswap V4 Position Retrieval`);
    console.log(`   Subgraph: ${SUBGRAPH_URL}`);
    console.log(`   Address:  ${TARGET_ADDRESS}\n`);

    const query = `
        {
            modifyLiquidities(
                where: { origin: "${TARGET_ADDRESS.toLowerCase()}" },
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
        }
    `;

    try {
        const response = await fetch(SUBGRAPH_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });

        const json = await response.json();

        if (json.errors) {
            console.error('❌ GraphQL Errors:', JSON.stringify(json.errors, null, 2));
            return;
        }

        const events = json.data.modifyLiquidities || [];
        console.log(`ℹ️  Found ${events.length} liquidity events.`);

        if (events.length === 0) {
            console.log('⚠️  No events found. This might be correct if the address has no V4 activity.');
            return;
        }

        // Reconstruction Logic
        const positionsMap = {};

        events.forEach(evt => {
            const poolId = evt.pool.id;
            const tL = evt.tickLower;
            const tU = evt.tickUpper;
            // Synthetic ID
            const posKey = `${poolId}-${tL}-${tU}-${evt.pool.feeTier}`;

            if (!positionsMap[posKey]) {
                positionsMap[posKey] = {
                    id: posKey,
                    pool: {
                        id: evt.pool.id,
                        fee: evt.pool.feeTier,
                        tick: parseInt(evt.pool.tick || 0),
                        token0Price: evt.pool.token0Price,
                        token1Price: evt.pool.token1Price
                    },
                    token0: evt.pool.token0,
                    token1: evt.pool.token1,
                    tickLower: parseInt(tL),
                    tickUpper: parseInt(tU),
                    liquidity: BigInt(0)
                };
            }

            if (evt.amount) {
                positionsMap[posKey].liquidity += BigInt(evt.amount);
            }
        });

        // Filter and Display
        const activePositions = Object.values(positionsMap)
            .filter(p => p.liquidity > 0n)
            .map(p => ({
                ...p,
                liquidity: p.liquidity.toString()
            }));

        console.log(`\n✅ Reconstruction Complete: ${activePositions.length} Active Positions\n`);

        activePositions.forEach((p, idx) => {
            const dec0 = parseInt(p.token0.decimals);
            const dec1 = parseInt(p.token1.decimals);
            const amounts = getAmountsForLiquidity(
                p.liquidity,
                parseInt(p.pool.tick),
                p.tickLower,
                p.tickUpper,
                dec0,
                dec1
            );

            console.log(`[${idx + 1}] ${p.token0.symbol}/${p.token1.symbol}`);
            console.log(`    ID: ${p.id}`);
            console.log(`    Liquidity: ${p.liquidity}`);
            console.log(`    Assets: ${amounts.amount0.toFixed(4)} ${p.token0.symbol} + ${amounts.amount1.toFixed(4)} ${p.token1.symbol}`);
            console.log(`    Unclaimed Fees: N/A (Subgraph missing feeGrowth fields)`);
            console.log(`    Range: [${p.tickLower}, ${p.tickUpper}]`);
            console.log(`    Current Tick: ${p.pool.tick}`);
            // console.log(`    Prices: ${p.pool.token0Price} / ${p.pool.token1Price}`); // Redundant if we show assets? Keeping it clean.
            console.log('---------------------------------------------------');
        });

    } catch (error) {
        console.error('❌ Fetch failed:', error);
    }
}

// === Math Helpers ===

function getSqrtRatioAtTick(tick) {
    return Math.sqrt(Math.pow(1.0001, tick));
}

function getAmountsForLiquidity(liquidityStr, currentTick, tickLower, tickUpper, dec0, dec1) {
    const L = parseFloat(liquidityStr);
    if (isNaN(L) || L === 0) return { amount0: 0, amount1: 0 };

    const sa = getSqrtRatioAtTick(tickLower);
    const sb = getSqrtRatioAtTick(tickUpper);
    let sp = getSqrtRatioAtTick(currentTick);

    // Clamp
    if (currentTick < tickLower) sp = sa;
    if (currentTick > tickUpper) sp = sb;

    let amount0 = 0, amount1 = 0;

    if (currentTick < tickLower) {
        amount0 = L * (sb - sa) / (sa * sb);
    } else if (currentTick >= tickUpper) {
        amount1 = L * (sb - sa);
    } else {
        amount0 = L * (sb - sp) / (sp * sb);
        amount1 = L * (sp - sa);
    }

    return {
        amount0: amount0 / Math.pow(10, dec0),
        amount1: amount1 / Math.pow(10, dec1)
    };
}

// Check for node-fetch or use global fetch (Node 18+)
if (!global.fetch) {
    console.warn('⚠️  Native fetch not found. This script requires Node.js 18+ or node-fetch.');
} else {
    testV4Positions();
}
