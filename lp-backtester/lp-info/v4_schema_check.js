
// Using native fetch provided by Node.js


const SUBGRAPH_URL = "https://gateway.thegraph.com/api/62159846067c13ee2999787a41ec0a13/subgraphs/id/DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G";

async function querySubgraph(query) {
    try {
        const response = await fetch(SUBGRAPH_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });
        const json = await response.json();
        return json;
    } catch (error) {
        console.error("Error fetching:", error);
    }
}

async function checkSchema() {
    console.log("Checking for Position entity...");
    const positionQuery = `
    {
        positions(first: 3) {
            id
            owner
            # liquidity  <-- Commented out, suspected missing
            # tickLower
            # tickUpper
            pool { id }
        }
    }
    `;
    const posResult = await querySubgraph(positionQuery);
    console.log("Positions:", JSON.stringify(posResult, null, 2));

    console.log("Checking for LiquidityPosition entity...");
    const liqPosQuery = `
    {
        liquidityPositions(first: 3) {
            id
            user { id }
            pool { id }
            liquidity
            tickLower
            tickUpper
        }
    }
    `;
    // We try to fetch. If entity doesn't exist, Graph returns error.
    const liqPosResult = await querySubgraph(liqPosQuery);
    console.log("LiquidityPositions:", JSON.stringify(liqPosResult, null, 2));

    // Also check pool structure while we are at it
    console.log("Checking Pool entity...");
    const poolQuery = `
    {
        pools(first: 1) {
            id
            currency0 { id symbol decimals } # Check if it's currency0 or token0
            currency1 { id symbol decimals }
            token0 { id symbol decimals }
            token1 { id symbol decimals }
            feeTier
            liquidity
            tick
            sqrtPrice
        }
    }
    `;
    const poolResult = await querySubgraph(poolQuery);
    console.log("Pools:", JSON.stringify(poolResult, null, 2));
}

checkSchema();
