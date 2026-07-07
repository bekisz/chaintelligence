/**
 * Unit tests for pool URL generation
 * Run with: node web/static/test_pool_urls.js
 */

// Pool URL generator functions (copied from app.js)
function generateUniswapUrl(network, poolAddress) {
  const networkLower = (network || 'ethereum').toLowerCase();
  let uniNetwork = 'ethereum';
  if (networkLower.includes('base')) {
    uniNetwork = 'base';
  } else if (networkLower.includes('eth')) {
    uniNetwork = 'ethereum';
  } else if (networkLower.includes('bnb') || networkLower.includes('bsc')) {
    uniNetwork = 'bnb';
  } else if (networkLower.includes('arbitrum')) {
    uniNetwork = 'arbitrum';
  } else if (networkLower.includes('optimism')) {
    uniNetwork = 'optimism';
  } else if (networkLower.includes('polygon')) {
    uniNetwork = 'polygon';
  }
  return `https://app.uniswap.org/explore/pools/${uniNetwork}/${poolAddress}`;
}

function generatePancakeUrl(network, poolAddress) {
  const networkLower = (network || 'ethereum').toLowerCase();
  let pChain = 'bsc';
  if (networkLower.includes('base')) {
    pChain = 'base';
  } else if (networkLower.includes('eth')) {
    pChain = 'eth';
  } else if (networkLower.includes('arbitrum')) {
    pChain = 'arb';
  }
  return `https://pancakeswap.finance/info/v3/pairs/${poolAddress}?chain=${pChain}`;
}

// Test cases
const testPool = '0x1234567890abcdef1234567890abcdef12345678';
const tests = [
  // Uniswap V3 tests
  { type: 'uniswap', network: 'Base', expected: 'base' },
  { type: 'uniswap', network: 'Ethereum', expected: 'ethereum' },
  { type: 'uniswap', network: 'Arbitrum', expected: 'arbitrum' },
  { type: 'uniswap', network: 'BNB', expected: 'bnb' },
  { type: 'uniswap', network: 'Polygon', expected: 'polygon' },
  { type: 'uniswap', network: 'Optimism', expected: 'optimism' },

  // PancakeSwap tests
  { type: 'pancake', network: 'Base', expected: 'base' },
  { type: 'pancake', network: 'Ethereum', expected: 'eth' },
  { type: 'pancake', network: 'Arbitrum', expected: 'arb' },
  { type: 'pancake', network: 'BNB', expected: 'bsc' },
];

// Run tests
console.log('=== Pool URL Generation Tests ===\n');
let passed = 0;
let failed = 0;

tests.forEach(test => {
  let url, expectedInUrl;
  if (test.type === 'uniswap') {
    url = generateUniswapUrl(test.network, testPool);
    expectedInUrl = `/${test.expected}/`;
  } else {
    url = generatePancakeUrl(test.network, testPool);
    expectedInUrl = `chain=${test.expected}`;
  }

  const success = url.includes(expectedInUrl);
  if (success) {
    console.log(`✓ ${test.type.toUpperCase()}/${test.network}: ${url}`);
    passed++;
  } else {
    console.log(`✗ ${test.type.toUpperCase()}/${test.network}: ${url}`);
    console.log(`  Expected to contain: ${expectedInUrl}`);
    failed++;
  }
});

console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
process.exit(failed > 0 ? 1 : 0);