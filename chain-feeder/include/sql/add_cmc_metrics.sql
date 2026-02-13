-- Add CoinMarketCap metrics columns to coin table

ALTER TABLE coin ADD COLUMN IF NOT EXISTS percent_change_1h NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS percent_change_24h NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS percent_change_7d NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS percent_change_30d NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS percent_change_60d NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS percent_change_90d NUMERIC;

ALTER TABLE coin ADD COLUMN IF NOT EXISTS market_cap NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS market_cap_dominance NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS fully_diluted_market_cap NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS tvl NUMERIC;

ALTER TABLE coin ADD COLUMN IF NOT EXISTS total_supply NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS circulating_supply NUMERIC;
ALTER TABLE coin ADD COLUMN IF NOT EXISTS max_supply NUMERIC;

ALTER TABLE coin ADD COLUMN IF NOT EXISTS cmc_last_updated TIMESTAMP WITH TIME ZONE;

-- Add comment for documentation
COMMENT ON COLUMN coin.percent_change_1h IS 'CoinMarketCap 1-hour price change percentage';
COMMENT ON COLUMN coin.percent_change_24h IS 'CoinMarketCap 24-hour price change percentage';
COMMENT ON COLUMN coin.percent_change_7d IS 'CoinMarketCap 7-day price change percentage';
COMMENT ON COLUMN coin.percent_change_30d IS 'CoinMarketCap 30-day price change percentage';
COMMENT ON COLUMN coin.percent_change_60d IS 'CoinMarketCap 60-day price change percentage';
COMMENT ON COLUMN coin.percent_change_90d IS 'CoinMarketCap 90-day price change percentage';
COMMENT ON COLUMN coin.market_cap IS 'CoinMarketCap market capitalization in USD';
COMMENT ON COLUMN coin.market_cap_dominance IS 'CoinMarketCap market cap dominance percentage';
COMMENT ON COLUMN coin.fully_diluted_market_cap IS 'CoinMarketCap fully diluted market cap in USD';
COMMENT ON COLUMN coin.tvl IS 'CoinMarketCap Total Value Locked';
COMMENT ON COLUMN coin.total_supply IS 'Total supply of coins';
COMMENT ON COLUMN coin.circulating_supply IS 'Circulating supply of coins';
COMMENT ON COLUMN coin.max_supply IS 'Maximum supply of coins';
COMMENT ON COLUMN coin.cmc_last_updated IS 'Last update timestamp from CoinMarketCap';
