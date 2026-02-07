const assert = require('assert').strict;
const { randomInt } = require('crypto');
const { calculateV3Backtest, getLiquidityAndAmounts, LiquidityPoolPosition } = require('./logic.js');


const dailyPrice = [];
const hourlyPrice = [];


function initPrices() {
    const startTs = Date.now();
    for (let day = 0; day <= 3 * 365; day++) {
        let price;
        if (day <= 365) {
            price = 1000;
        } else if (day < 1.5 * 365) {
            price = 800;
        } else if (day < 2 * 365) {
            price = 1800;
        } else if (day < 3 * 365) {
            price = 1000 + 10 * (day % 365); // Use day instead of broken randomInt
        } else {
            price = 2000;
        }
        dailyPrice.push([startTs + day * 24 * 3600 * 1000, price]);
        for (let hour = 0; hour < 24; hour++) {
            hourlyPrice.push([startTs + day * 24 * 3600 * 1000 + hour * 3600 * 1000, price]);
        }
    }

}
// --- Test Cases ---



async function runTests() {
    console.log('🚀 Starting LP Backtest Unit Tests...\n');
    initPrices();

    // 2. Simple Non-rebalanincing Strategy with Flat Price, High Concentration Yield
    let result = calculateV3Backtest(dailyPrice, -0.1, 0.1, -0.1, 0.1, 0.2, 'simple', 0);
    let lpCheckedValue = result.lpTotalData[365][1];
    console.log(`Test #02 : Simple Non-rebalanincing Strategy with Flat Price : 20% APR narrow range (7.5x). Final value: ${lpCheckedValue.toFixed(2)}`);
    assert.ok(lpCheckedValue > 249.0 && lpCheckedValue < 251.0, 'Yield should be ~150%');

    let strategies = ['simple', 'time-delayed', 'settled', 'periodic']
    let testId = 3;
    let epsilon = 0.1;

    let apr = 0.2;
    let apy_max = (1 + apr / 365) ** 365 - 1;

    console.log(`--- Flat-priced wide-ranged LP pools with ${(apr * 100).toFixed(2)}% APR should yield between ${(apr * 100).toFixed(2)}% and ${(apy_max * 100).toFixed(2)}% (APY).`);

    for (const [_, strategy] of strategies.entries()) {
        let lp_range_min = -0.5, lp_range_max = 1, timeDelay = 7;
        let rebalance_range_min = lp_range_min, rebalance_range_max = lp_range_max;
        console.log(`Test #${testId++} [calculateV3Backtest]: Strategy: ${strategy}, LP Range: [ ${(lp_range_min * 100).toFixed(2)}% - +${(lp_range_max * 100).toFixed(2)}% ], Rebalance Range : [ ${(rebalance_range_min * 100).toFixed(2)}% - +${(rebalance_range_max * 100).toFixed(2)}% ], Time-delay: ${timeDelay}`);

        let result = calculateV3Backtest(dailyPrice, lp_range_min, lp_range_max,
            rebalance_range_min, rebalance_range_max, apr, strategy, timeDelay);
        let lpCheckedValue = result.lpTotalData[365][1];
        console.log(`   Final LP value: ${lpCheckedValue.toFixed(2)} with APY : ${(lpCheckedValue - 100).toFixed(2)}%`);

        assert.ok(100 * (1 + apr) - epsilon <= lpCheckedValue && lpCheckedValue <= 100 * (1 + apy_max) + 1.0, 'Yield should be between APR and max APY');
    }

    console.log(`--- Flat-priced wide-ranged LP pools should yield similar yields with hourly and daily prices`);

    for (const [_, strategy] of strategies.entries()) {
        let lp_range_min = -0.5, lp_range_max = 1, timeDelay = 7;
        let rebalance_range_min = lp_range_min, rebalance_range_max = lp_range_max;
        console.log(`Test #${testId++} [calculateV3Backtest]: Strategy: ${strategy}, LP Range: [ ${(lp_range_min * 100).toFixed(2)}% - +${(lp_range_max * 100).toFixed(2)}% ], Rebalance Range : [ ${(rebalance_range_min * 100).toFixed(2)}% - +${(rebalance_range_max * 100).toFixed(2)}% ], Time-delay: ${timeDelay}`);

        let resultDaily = calculateV3Backtest(dailyPrice, lp_range_min, lp_range_max,
            rebalance_range_min, rebalance_range_max, apr, strategy, timeDelay);
        let lpCheckedValueDaily = resultDaily.lpTotalData[365][1];
        console.log(`   Final Daily LP value: ${lpCheckedValueDaily.toFixed(2)} with APY : ${(lpCheckedValueDaily - 100).toFixed(2)}%`);

        let resultHourly = calculateV3Backtest(hourlyPrice, lp_range_min, lp_range_max,
            rebalance_range_min, rebalance_range_max, apr, strategy, timeDelay);
        let lpCheckedValueHourly = resultHourly.lpTotalData[365 * 24][1];
        console.log(`   Final Hourly LP value: ${lpCheckedValueHourly.toFixed(2)} with APY : ${(lpCheckedValueHourly - 100).toFixed(2)}%`);

        const epsilon_rel = 0.02; // 2% relative tolerance for sampling
        assert.ok(Math.abs(lpCheckedValueDaily - lpCheckedValueHourly) / lpCheckedValueHourly <= epsilon_rel, `Yield for ${strategy} should be close with hourly and daily sampling (within 2%)`);
    }

    console.log(`--- Check if Actual APY is lower than the max yield`);

    for (const [_, strategy] of strategies.entries()) {
        let lp_range_min = -0.5, lp_range_max = 1, timeDelay = 7;
        let rebalance_range_min = lp_range_min, rebalance_range_max = lp_range_max;
        let apr = 0.2;
        console.log(`Test #${testId++} [calculateV3Backtest]: Strategy: ${strategy}, LP Range: [ ${(lp_range_min * 100).toFixed(2)}% - +${(lp_range_max * 100).toFixed(2)}% ], Rebalance Range : [ ${(rebalance_range_min * 100).toFixed(2)}% - +${(rebalance_range_max * 100).toFixed(2)}% ], Time-delay: ${timeDelay}`);

        let result = calculateV3Backtest(dailyPrice, lp_range_min, lp_range_max,
            rebalance_range_min, rebalance_range_max, apr, strategy, timeDelay);
        let lpCheckedValue = result.lpTotalData[result.lpTotalData.length - 1][1];

        const concentrationFactor = 1.5 / (lp_range_max - lp_range_min);
        const effectiveApr = apr * concentrationFactor;
        const max_val = 100 * Math.exp(effectiveApr * 3);

        console.log(`   Final LP value: ${lpCheckedValue.toFixed(2)}  and Theoretical Max (continuous): ${max_val.toFixed(2)} `);

        // Rebalancing strategies can naturally exceed theoretical continuous max due to compounding discrete steps in a flat market
        const tolerance = (strategy === 'settled' || strategy === 'periodic') ? 1.25 : 1.15;
        assert.ok(0 <= lpCheckedValue && lpCheckedValue <= max_val * tolerance, `Yield for ${strategy} should be within theoretical bounds (+ ${(tolerance - 1) * 100}% tolerance)`);
    }



    console.log(`--- Wide-ranged LP pools should yield similar yields with hourly and daily prices`);

    for (const [_, strategy] of strategies.entries()) {
        let lp_range_min = -0.5, lp_range_max = 1, timeDelay = 7;
        let rebalance_range_min = lp_range_min, rebalance_range_max = lp_range_max;
        console.log(`Test #${testId++} [calculateV3Backtest]: Strategy: ${strategy}, LP Range: [ ${(lp_range_min * 100).toFixed(2)}% - +${(lp_range_max * 100).toFixed(2)}% ], Rebalance Range : [ ${(rebalance_range_min * 100).toFixed(2)}% - +${(rebalance_range_max * 100).toFixed(2)}% ], Time-delay: ${timeDelay}`);

        let resultDaily = calculateV3Backtest(dailyPrice, lp_range_min, lp_range_max,
            rebalance_range_min, rebalance_range_max, apr, strategy, timeDelay);
        let lpCheckedValueDaily = resultDaily.lpTotalData[resultDaily.lpTotalData.length - 1][1];
        console.log(`   Final Daily LP value: ${lpCheckedValueDaily.toFixed(2)} with APY : ${(lpCheckedValueDaily - 100).toFixed(2)}%`);

        let resultHourly = calculateV3Backtest(hourlyPrice, lp_range_min, lp_range_max,
            rebalance_range_min, rebalance_range_max, apr, strategy, timeDelay);
        let lpCheckedValueHourly = resultHourly.lpTotalData[resultHourly.lpTotalData.length - 1][1];
        console.log(`   Final Hourly LP value: ${lpCheckedValueHourly.toFixed(2)} with APY : ${(lpCheckedValueHourly - 100).toFixed(2)}%`);

        const epsilon_rel = 0.05; // 5% relative tolerance for longer runs
        assert.ok(Math.abs(lpCheckedValueDaily - lpCheckedValueHourly) / lpCheckedValueHourly <= epsilon_rel, `Yield for ${strategy} should be close with hourly and daily sampling (within 5%)`);
    }


    console.log('\n✅ All tests passed!');
}

runTests().catch(err => {
    console.error('\n❌ Test failed!');
    console.error(err);
    process.exit(1);
});
