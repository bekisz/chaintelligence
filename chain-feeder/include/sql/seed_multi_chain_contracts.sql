-- Seed new coins
INSERT INTO coin (symbol, hardness) VALUES
('USDC.E', 990),
('USDBC', 990),
('BTCB', 870),
('BUSD', 950),
('CAKE', 600),
('BETH', 860),
('FDUSD', 980),
('TUSD', 960),
('GMX', 650),
('ZRO', 680),
('WBNB', 800)
ON CONFLICT (symbol) DO NOTHING;

-- Seed contracts
INSERT INTO coin_contract (coin_id, chain, contract_address, is_native) VALUES
-- Ethereum Native / Sentinels
((SELECT coin_id FROM coin WHERE symbol='ETH'), 'ethereum', '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', TRUE),
-- Arbitrum Contracts
((SELECT coin_id FROM coin WHERE symbol='ETH'), 'arbitrum', '0x0000000000000000000000000000000000000000', TRUE),
((SELECT coin_id FROM coin WHERE symbol='USDC.E'), 'arbitrum', '0xff970a61a04b1ca14834a43f5de4533ebddb5cc8', FALSE),
((SELECT coin_id FROM coin WHERE symbol='USDC'), 'arbitrum', '0xaf88d065e77c8cc2239327c5edb3a432268e5831', FALSE),
((SELECT coin_id FROM coin WHERE symbol='WETH'), 'arbitrum', '0x82af49447d8a07e3bd95bd0d56f35241523fbab1', FALSE),
((SELECT coin_id FROM coin WHERE symbol='USDT'), 'arbitrum', '0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9', FALSE),
((SELECT coin_id FROM coin WHERE symbol='WBTC'), 'arbitrum', '0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f', FALSE),
((SELECT coin_id FROM coin WHERE symbol='DAI'), 'arbitrum', '0xda10009c55681e77d502082691d29f8fb095569f', FALSE),
((SELECT coin_id FROM coin WHERE symbol='LINK'), 'arbitrum', '0xf97f4df75117a78c1a5a0dbb814af92458539fb4', FALSE),
((SELECT coin_id FROM coin WHERE symbol='GMX'), 'arbitrum', '0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a', FALSE),
((SELECT coin_id FROM coin WHERE symbol='AAVE'), 'arbitrum', '0xba5ddd1f9d7f570dc94a51479a000e3bce967196', FALSE),
((SELECT coin_id FROM coin WHERE symbol='ZRO'), 'arbitrum', '0x6985884c4392d348587b19cb9eaaf157f13271cd', FALSE),
-- Base Contracts
((SELECT coin_id FROM coin WHERE symbol='ETH'), 'base', '0x0000000000000000000000000000000000000000', TRUE),
((SELECT coin_id FROM coin WHERE symbol='WETH'), 'base', '0x4200000000000000000000000000000000000006', FALSE),
((SELECT coin_id FROM coin WHERE symbol='USDC'), 'base', '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913', FALSE),
((SELECT coin_id FROM coin WHERE symbol='USDBC'), 'base', '0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca', FALSE),
((SELECT coin_id FROM coin WHERE symbol='CBBTC'), 'base', '0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf', FALSE),
-- BNB/BSC Contracts
((SELECT coin_id FROM coin WHERE symbol='BNB'), 'bsc', '0x0000000000000000000000000000000000000000', TRUE),
((SELECT coin_id FROM coin WHERE symbol='WBNB'), 'bsc', '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', FALSE),
((SELECT coin_id FROM coin WHERE symbol='USDC'), 'bsc', '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d', FALSE),
((SELECT coin_id FROM coin WHERE symbol='USDT'), 'bsc', '0x55d398326f99059ff775485246999027b3197955', FALSE),
((SELECT coin_id FROM coin WHERE symbol='BTCB'), 'bsc', '0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c', FALSE),
((SELECT coin_id FROM coin WHERE symbol='BUSD'), 'bsc', '0xe9e7cea3dedca5984780bafc599bd69add087d56', FALSE),
((SELECT coin_id FROM coin WHERE symbol='CAKE'), 'bsc', '0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82', FALSE),
((SELECT coin_id FROM coin WHERE symbol='BETH'), 'bsc', '0x25063237838e18f49999b541a61962c641914a81', FALSE),
((SELECT coin_id FROM coin WHERE symbol='FDUSD'), 'bsc', '0x3813e82e6f7098b9583fc0f33a962d02018b6803', FALSE),
((SELECT coin_id FROM coin WHERE symbol='TUSD'), 'bsc', '0x14016e85a25aeb13065688e4b4a6e8f5679bee99', FALSE),
((SELECT coin_id FROM coin WHERE symbol='ETH'), 'bsc', '0x2170ed0880ac9a755fd29b2688956bd959f933f8', FALSE)
ON CONFLICT (coin_id, chain) DO UPDATE SET contract_address = EXCLUDED.contract_address;
