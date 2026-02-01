-- Create the data warehouse database
CREATE DATABASE chaintelligence;

-- Switch to the new database to create tables
\c chaintelligence;

-- 1. COIN TABLE (Symbol as PK, VARCHAR(8))
CREATE TABLE IF NOT EXISTS coin (
    symbol VARCHAR(8) PRIMARY KEY, -- Primary Key, Not Null, Unique, Max 8 chars
    hardness INTEGER DEFAULT 0,
    family VARCHAR(50),
    image_url TEXT
);

-- Index for case-insensitive lookup (though we force upper)
CREATE UNIQUE INDEX IF NOT EXISTS coin_symbol_idx ON coin (UPPER(symbol));

-- 2. LIQUIDITY POOL TABLE
CREATE TABLE IF NOT EXISTS liquidity_pool (
    id SERIAL PRIMARY KEY,
    network VARCHAR(20) NOT NULL,
    protocol VARCHAR(20) NOT NULL,
    pool_name VARCHAR(255) NOT NULL,
    fee_tier VARCHAR(10),
    -- References changed to symbol
    coin0_symbol VARCHAR(8) REFERENCES coin(symbol),
    coin1_symbol VARCHAR(8) REFERENCES coin(symbol),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(network, protocol, pool_name, fee_tier)
);

CREATE INDEX IF NOT EXISTS liquidity_pool_coin0_upper_idx ON liquidity_pool (UPPER(coin0_symbol));
CREATE INDEX IF NOT EXISTS liquidity_pool_coin1_upper_idx ON liquidity_pool (UPPER(coin1_symbol));

-- 3. POSITION TABLE
CREATE TABLE IF NOT EXISTS liquidity_pool_position (
    id SERIAL PRIMARY KEY,
    pool_id INT REFERENCES liquidity_pool(id),
    position_key VARCHAR(100) NOT NULL UNIQUE,
    wallet_address VARCHAR(42) NOT NULL,
    token_id VARCHAR(50),
    tick_lower INTEGER,
    tick_upper INTEGER,
    price_lower NUMERIC,
    price_upper NUMERIC,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. SNAPSHOT TABLE
CREATE TABLE IF NOT EXISTS liquidity_pool_position_snapshot (
    id SERIAL PRIMARY KEY,
    position_id INT REFERENCES liquidity_pool_position(id),
    timestamp TIMESTAMP NOT NULL,
    balance_usd NUMERIC,
    
    -- Flattened Assets (Corresponding to Pool Coin0/Coin1)
    coin0_amount NUMERIC,
    coin1_amount NUMERIC,

    -- Flattened Claimable (Unclaimed Rewards matching Pool Coins)
    -- This assumes rewards are ALWAYS in the pool's tokens.
    coin0_claimable_amount NUMERIC,
    coin1_claimable_amount NUMERIC,
    
    -- Flattened Claimed (Cumulative collected fees matching Pool Coins)
    coin0_claimed_amount NUMERIC,
    coin1_claimed_amount NUMERIC,
    
    current_tick INTEGER,
    current_price NUMERIC,
    in_range BOOLEAN
);

-- 5. UNISWAP V3 SWAPS (Existing table)
CREATE TABLE IF NOT EXISTS uniswap_v3_swaps (
    id VARCHAR(255) PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    tx_hash VARCHAR(66) NOT NULL,
    token0_address VARCHAR(42) NOT NULL,
    token1_address VARCHAR(42) NOT NULL,
    token0_symbol VARCHAR(100),
    token1_symbol VARCHAR(100),
    amount0 NUMERIC,
    amount1 NUMERIC,
    amount_usd NUMERIC,
    fee_tier VARCHAR(20)
);

CREATE INDEX IF NOT EXISTS idx_swaps_timestamp ON uniswap_v3_swaps(timestamp);
CREATE INDEX IF NOT EXISTS idx_swaps_token0 ON uniswap_v3_swaps(token0_symbol);
CREATE INDEX IF NOT EXISTS idx_swaps_token1 ON uniswap_v3_swaps(token1_symbol);


-- 6. TRIGGERS
CREATE OR REPLACE FUNCTION enforce_uppercase_symbols() 
RETURNS TRIGGER AS $$
BEGIN
  -- Truncate to 8 chars first to respect schema
  NEW.symbol = LEFT(UPPER(NEW.symbol), 8);
  IF NEW.family IS NULL THEN
     NEW.family = NEW.symbol;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_coin_upper 
BEFORE INSERT OR UPDATE ON coin
FOR EACH ROW EXECUTE FUNCTION enforce_uppercase_symbols();

CREATE OR REPLACE FUNCTION enforce_uppercase_pool_symbols() 
RETURNS TRIGGER AS $$
BEGIN
  NEW.coin0_symbol = LEFT(UPPER(NEW.coin0_symbol), 8);
  NEW.coin1_symbol = LEFT(UPPER(NEW.coin1_symbol), 8);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pool_upper 
BEFORE INSERT OR UPDATE ON liquidity_pool
FOR EACH ROW EXECUTE FUNCTION enforce_uppercase_pool_symbols();

-- 7. INITIAL DATA

INSERT INTO coin (symbol, hardness, family) VALUES
('USDC', 1000, 'USD'),
('USDT', 990, 'USD'),
('USDS', 980, 'USD'),
('DAI', 970, 'USD'),
('USDE', 960, 'USD'),
('GHO', 950, 'USD'),
('EURC', 940, 'EUR'),
('EURCV', 930, 'EUR'),
('EURI', 920, 'EUR'),
('EURQ', 910, 'EUR'),
('ZCHF', 900, 'CHF'),
('PAXG', 890, 'GOLD'),
('XAUt', 880, 'GOLD'),
('BTC', 870, 'BTC'),
('WBTC', 870, 'BTC'),
('ETH', 860, 'ETH'),
('WETH', 860, 'ETH'),
('stETH', 859, 'ETH'),
('wstETH', 859, 'ETH'),
('LINK', 850, 'LINK'),
('UNI', 840, 'UNI'),
('SKY', 830, 'SKY'),
('AAVE', 820, 'AAVE'),
('stAAVE', 800, 'AAVE'),
('STKAAVE', 800, 'AAVE'),
('clAAVE', 800, 'AAVE'), -- Mapped from CLAIMABLE AAVE ON STKAAVE
('stkGHO', 800, 'USD'),
('sGHO', 800, 'USD'),
('cbBTC', 800, 'BTC'),
('RLUSD', 800, 'USD'),
('sUSDS', 800, 'USD') -- Mapped from SAVINGS USDS
ON CONFLICT (symbol) DO NOTHING;

-- 8. BACKWARD COMPATIBILITY VIEWS
-- Reconstructing the legacy view without external reward table
CREATE OR REPLACE VIEW v_lp_snapshots_summary AS
SELECT 
    s.id,
    s.timestamp,
    pos.wallet_address AS address,
    pos.position_key,
    pool.protocol,
    pool.network,
    CASE 
        WHEN pos.token_id IS NOT NULL THEN pool.pool_name || ' (Token ID: ' || pos.token_id || ')' 
        ELSE pool.pool_name 
    END AS position_label,
    s.balance_usd,
    
    -- Reconstruct Assets
    pool.coin0_symbol as asset0_symbol,
    s.coin0_amount as asset0_amount,
    0 as asset0_usd, 
    pool.coin1_symbol as asset1_symbol,
    s.coin1_amount as asset1_amount,
    0 as asset1_usd,
    
    -- Reconstruct Rewards (Use Claimable Columns)
    pool.coin0_symbol as reward0_symbol,
    s.coin0_claimable_amount as reward0_amount,
    0 as reward0_usd,
    pool.coin1_symbol as reward1_symbol,
    s.coin1_claimable_amount as reward1_amount,
    0 as reward1_usd,

    -- Reconstruct JSONs for legacy app
    jsonb_build_array(
        jsonb_build_object('symbol', pool.coin0_symbol, 'balance', s.coin0_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', pool.coin1_symbol, 'balance', s.coin1_amount, 'balanceUSD', 0)
    ) as assets,
    
    jsonb_build_array(
        jsonb_build_object('symbol', pool.coin0_symbol, 'balance', s.coin0_claimable_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', pool.coin1_symbol, 'balance', s.coin1_claimable_amount, 'balanceUSD', 0)
    ) as unclaimed,
     
    -- Images (From Coins)
    jsonb_build_array(
        (SELECT image_url FROM coin WHERE symbol = pool.coin0_symbol),
        (SELECT image_url FROM coin WHERE symbol = pool.coin1_symbol)
    ) as images,
    
    -- Total unclaimed (Approximation, no USD value stored yet)
    0 as total_unclaimed_usd,
    
    -- Range data
    pos.token_id,
    pos.tick_lower,
    pos.tick_upper,
    s.current_tick,
    pos.price_lower,
    pos.price_upper,
    s.current_price,
    s.in_range,
    pool.fee_tier
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id;
