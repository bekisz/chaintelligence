const assert = require('assert').strict;
const { calculateV3Backtest, getLikidityAndAmounts } = require('./app.js');

// --- Test Cases ---

async function runTests() {
    console.log('🚀 Starting LP Backtest Unit Tests...\n');

    // 1. Base Yield Accuracy
    const oneYearDaily = [];
    const startTs = Date.now();
    for (let i = 0; i <= 365; i++) {
        oneYearDaily.push([startTs + i * 24 * 3600 * 1000, 1000]); // Flat price
    }
    const resBase = calculateV3Backtest(oneYearDaily, -0.5, 1.0, -0.5, 1.0, 0.2, 'simple', 0);
    const finalVal = resBase.lpTotalData[resBase.lpTotalData.length - 1][1];
    console.log(`Test #1 (Base Yield): 20% APR flat price. Final value: ${finalVal.toFixed(2)}`);
    assert.ok(finalVal > 119.9 && finalVal < 120.1, 'Yield should be exactly 20%');

    // 2. High Concentration Yield
    const resConc = calculateV3Backtest(oneYearDaily, -0.1, 0.1, -0.1, 0.1, 0.2, 'simple', 0);
    const finalValConc = resConc.lpTotalData[resConc.lpTotalData.length - 1][1];
    console.log(`Test #2 (Concentrated): 20% APR narrow range (7.5x). Final value: ${finalValConc.toFixed(2)}`);
    assert.ok(finalValConc > 249.9 && finalValConc < 250.1, 'Yield should be ~150%');

    // 3. Periodic Rebalancing
    const periodicPrices = [];
    for (let i = 0; i <= 60; i++) {
        periodicPrices.push([startTs + i * 24 * 3600 * 1000, i < 15 ? 1000 : 800]);
    }
    const resSimple = calculateV3Backtest(periodicPrices, -0.5, 1.0, -0.5, 1.0, 0.1, 'simple', 0);
    const resPeriodic = calculateV3Backtest(periodicPrices, -0.5, 1.0, -0.5, 1.0, 0.1, 'periodic', 30);
    console.log(`Test #3 (Periodic): Simple vs Periodic (30d) on 20% drop. Simple: ${resSimple.lpTotalData[60][1].toFixed(2)}, Periodic: ${resPeriodic.lpTotalData[60][1].toFixed(2)}`);
    assert.ok(resPeriodic.lpTotalData[60][1] > resSimple.lpTotalData[60][1], 'Periodic rebalancing should improve yield after price drop');

    // 4. Resolution Independence
    const dailyPrice = [];
    const hourlyPrice = [];
    for (let i = 0; i <= 30; i++) {
        const time = startTs + i * 24 * 3600 * 1000;
        dailyPrice.push([time, 1000]);
        for (let h = 0; h < 24; h++) {
            if (i === 30 && h > 0) break;
            hourlyPrice.push([time + h * 3600 * 1000, 1000]);
        }
    }
    const resDaily = calculateV3Backtest(dailyPrice, -0.5, 1.0, -0.5, 1.0, 0.2, 'simple', 0);
    const resHourly = calculateV3Backtest(hourlyPrice, -0.5, 1.0, -0.5, 1.0, 0.2, 'simple', 0);
    const valDaily = resDaily.lpTotalData[resDaily.lpTotalData.length - 1][1];
    const valHourly = resHourly.lpTotalData[resHourly.lpTotalData.length - 1][1];
    console.log(`Test #4 : Hourly and daily resolution should yield similar results with non-rebalancing strategy: Daily vs Hourly on 30d flat. Daily: ${valDaily.toFixed(4)}, Hourly: ${valHourly.toFixed(4)}`);
    assert.ok(Math.abs(valDaily - valHourly) < 0.001, 'Yields should be nearly identical regardless of frequency');

    console.log('\n✅ All tests passed!');
}

runTests().catch(err => {
    console.error('\n❌ Test failed!');
    console.error(err);
    process.exit(1);
});
