// Simple test to verify link generation for Arbitrum pools
function generateLinks(network, poolAddr) {
  const networkLower = network.toLowerCase();
  // Uniswap mapping
  let uniNetwork = 'ethereum';
  if (networkLower.includes('base')) uniNetwork = 'base';
  else if (networkLower.includes('eth')) uniNetwork = 'ethereum';
  else if (networkLower.includes('bnb') || networkLower.includes('bsc')) uniNetwork = 'bnb';
  else if (networkLower.includes('arbitrum')) uniNetwork = 'arbitrum';
  else if (networkLower.includes('optimism')) uniNetwork = 'optimism';
  else if (networkLower.includes('polygon')) uniNetwork = 'polygon';

  const uniswapLink = `https://app.uniswap.org/explore/pools/${uniNetwork}/${poolAddr}`;

  // PancakeSwap mapping
  let pChain = 'bsc';
  if (networkLower.includes('base')) pChain = 'base';
  else if (networkLower.includes('eth')) pChain = 'eth';
  else if (networkLower.includes('arbitrum')) pChain = 'arb';
  const pancakeLink = `https://pancakeswap.finance/info/v3/pairs/${poolAddr}?chain=${pChain}`;

  return { uniswapLink, pancakeLink };
}

const addr = '0x67D3E181E6dcC47f977c3A4b33Ac65454b87b997';
const links = generateLinks('Arbitrum', addr);
console.log('Uniswap link:', links.uniswapLink);
console.log('Pancake link:', links.pancakeLink);
