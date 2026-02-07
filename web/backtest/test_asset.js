const assert = require('assert').strict;
const { Asset } = require('./logic.js');

async function runAssetTests() {
    console.log("🚀 Starting Asset Class Unit Tests...");
    // Test 1 : User daily timeframe to get a price at a specific date  using CryptoCompare API
    console.log("\nTest #1: Real Data Integration (CryptoCompare API) using ETH daily data");
    const eth = new Asset('ETH');
    await eth.fetchHistory(useHourly = false, startTime = new Date("2025-12-01 6:00 UTC")); // daily as default
    const testDate = new Date("2025-12-26 7:30 UTC");
    console.log(`  Fetching real DAILY data for ETH and looking up price for ${testDate.toISOString()}...`);

    const ethPrice = eth.getPriceAt(testDate);
    console.log(`  ETH price at ${testDate.toISOString()}: $${ethPrice} and expected: $2984.66`);
    assert.ok(typeof ethPrice === 'number' && ethPrice > 2900 && ethPrice < 3000, "Price should be around $2984.66");
    console.log(`✅ Real data test passed! ETH price at index: $${ethPrice}`);
    // Test 2 : User hourly timeframe to get a price at a specific date  using CryptoCompare API
    console.log("\nTest #2: Real Data Integration (CryptoCompare API) using ETH hourly data");
    await eth.fetchHistory(useHourly = true, startTime = new Date("2025-12-01 6:00 UTC")); // daily as default
    console.log(`  Fetching real HOURLY data for ETH and looking up price for ${testDate.toISOString()}...`);

    const ethPriceHourly = eth.getPriceAt(testDate);
    console.log(`  ETH price at ${testDate.toISOString()}: $${ethPriceHourly} and expected: $2984.66`);
    assert.ok(typeof ethPriceHourly === 'number' && ethPriceHourly > 2900 && ethPriceHourly < 3000, "Price should be around $2984.66");
    console.log(`✅ Real data test passed! ETH price at index: $${ethPriceHourly}`);

    console.log("\nTest #3: Price Range Validation");
    const firstDataDate = eth.priceData[0][0];
    const lastDataDate = eth.priceData[eth.priceData.length - 1][0];
    const oneDayMs = 24 * 60 * 60 * 1000;

    // Within range - should pass
    console.log("  Checking date slightly before first data point (within 1 day)...");
    assert.doesNotThrow(() => eth.getPriceAt(new Date(firstDataDate - oneDayMs / 2)));

    // Equal to boundary - should pass
    console.log("  Checking date exactly 1 day before first data point...");
    assert.doesNotThrow(() => eth.getPriceAt(new Date(firstDataDate - oneDayMs)));

    // Outside range - should fail
    console.log("  Checking date more than 1 day before first data point (should throw)...");
    assert.throws(() => eth.getPriceAt(new Date(firstDataDate - oneDayMs - 1000)), /out of range/);

    // After range - should fail
    console.log("  Checking date more than 1 day after last data point (should throw)...");
    assert.throws(() => eth.getPriceAt(new Date(lastDataDate + oneDayMs + 1000)), /out of range/);

    console.log("✅ Price range validation tests passed!");

    console.log("\n✨ All Asset class tests passed successfully!");
}

runAssetTests().catch(err => {
    console.error("\n❌ Asset testing failed!");
    console.error(err);
    process.exit(1);
});
