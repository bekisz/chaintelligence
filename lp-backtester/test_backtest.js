const assert = require('assert').strict;
const { randomInt } = require('crypto');
const { calculateV3Backtest, getLikidityAndAmounts } = require('./app.js');


const dailyPrice = [];
const hourlyPrice = [];


function initPrices() {
    const startTs = Date.now();
    for (let day = 0; day <= 3 * 365; day++) {
        if (day <= 365) {
            price = 1000;
        } else if (day < 1.5 * 365) {
            price = 800;
        } else if (day < 2 * 365) {
            price = 1800;
        } else if (day < 3 * 365) {
            prince = 1000 + 10 * (randomInt % 365);
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
    let result = [];
    let lpCheckedValue = 0;
    // 1. Simple Non-rebalanincing Strategy with Flat Price, Base Yield Accuracy
    result = calculateV3Backtest(dailyPrice, -0.5, 1.0, -0.5, 1.0, 0.2, 'simple', 0);
    lpCheckedValue = result.lpTotalData[365][1];
    console.log(`Test #01 : Simple Non-rebalancing Strategy with Flat Price: Base Yield of 20% APR flat price. Final value: ${lpCheckedValue.toFixed(2)}`);
    assert.ok(lpCheckedValue > 119.9 && lpCheckedValue < 120.1, 'Yield should be exactly 20%');

    // 2. Simple Non-rebalanincing Strategy with Flat Price, High Concentration Yield
    result = calculateV3Backtest(dailyPrice, -0.1, 0.1, -0.1, 0.1, 0.2, 'simple', 0);
    lpCheckedValue = result.lpTotalData[365][1];
    console.log(`Test #02 : Simple Non-rebalanincing Strategy with Flat Price : 20% APR narrow range (7.5x). Final value: ${lpCheckedValue.toFixed(2)}`);
    assert.ok(lpCheckedValue > 249.9 && lpCheckedValue < 250.1, 'Yield should be ~150%');

    // 3. Simple weekly rebalanincing strategy with Flat Price
    result = calculateV3Backtest(dailyPrice, -0.1, 0.1, -0.1, 0.1, 0.2, 'periodic', 7);
    lpCheckedValue = resConc.lpTotalData[365][1];
    console.log(`Test #03 :  Periodic (Weekly) rebalanincing strategy with Flat Price. Final value: ${lpCheckedValue.toFixed(2)}`);
    assert.ok(lpCheckedValue > 249.9 && lpCheckedValue < 250.1, 'Yield should be ~150%');

    /*
    const resSimple = calculateV3Backtest(periodicPrices, -0.5, 1.0, -0.5, 1.0, 0.1, 'simple', 0);
    const resPeriodic = calculateV3Backtest(periodicPrices, -0.5, 1.0, -0.5, 1.0, 0.1, 'periodic', 30);
    console.log(`Test #03 : Simple vs Periodic (30d) on 20% drop. Simple: ${resSimple.lpTotalData[60][1].toFixed(2)}, Periodic: ${resPeriodic.lpTotalData[60][1].toFixed(2)}`);
    assert.ok(resPeriodic.lpTotalData[60][1] > resSimple.lpTotalData[60][1], 'Periodic rebalancing should improve yield after price drop');
    */
    // 4. Resolution Independence
    /*
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
    console.log(`Test #04 : [Resolution Independence] Hourly and daily resolution should yield similar results with non-rebalancing strategy: Daily vs Hourly on 30d flat. Daily: ${valDaily.toFixed(4)}, Hourly: ${valHourly.toFixed(4)}`);
    assert.ok(Math.abs(valDaily - valHourly) < 0.001, 'Yields should be nearly identical regardless of frequency');


    const resDaily = calculateV3Backtest(dailyPrice, -0.5, 1.0, -0.5, 1.0, 0.2, 'periodic', 0);
    const resHourly = calculateV3Backtest(hourlyPrice, -0.5, 1.0, -0.5, 1.0, 0.2, 'periodic', 0);
    const valDaily = resDaily.lpTotalData[resDaily.lpTotalData.length - 1][1];
    const valHourly = resHourly.lpTotalData[resHourly.lpTotalData.length - 1][1];
    console.log(`Test #04 : [Resolution Independence] Hourly and daily resolution should yield similar results with non-rebalancing strategy: Daily vs Hourly on 30d flat. Daily: ${valDaily.toFixed(4)}, Hourly: ${valHourly.toFixed(4)}`);
    assert.ok(Math.abs(valDaily - valHourly) < 0.001, 'Yields should be nearly identical regardless of frequency');

    */
    console.log('\n✅ All tests passed!');
}

runTests().catch(err => {
    console.error('\n❌ Test failed!');
    console.error(err);
    process.exit(1);
});
