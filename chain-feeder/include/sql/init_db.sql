-- Create the data warehouse database
CREATE DATABASE chaintelligence;

-- Switch to the new database to create tables
\c chaintelligence;

-- 1. COIN TABLE (Symbol as PK, VARCHAR(8))
CREATE TABLE IF NOT EXISTS coin (
    symbol VARCHAR(8) PRIMARY KEY, -- Primary Key, Not Null, Unique, Max 8 chars
    name VARCHAR(255),
    slug VARCHAR(255),
    hardness INTEGER DEFAULT 0,
    cmc_rank INTEGER,
    cmc_id INTEGER,
    ethereum_address VARCHAR(42) NOT NULL UNIQUE, -- Contract address as unique key
    first_historical_data TIMESTAMP,
    image_url TEXT,
    price NUMERIC,
    price_timestamp TIMESTAMP WITH TIME ZONE,
    decimals INTEGER DEFAULT 18,
    percent_change_1h NUMERIC,
    percent_change_24h NUMERIC,
    percent_change_7d NUMERIC,
    percent_change_30d NUMERIC,
    percent_change_60d NUMERIC,
    percent_change_90d NUMERIC,
    market_cap NUMERIC,
    market_cap_dominance NUMERIC,
    fully_diluted_market_cap NUMERIC,
    tvl NUMERIC,
    total_supply NUMERIC,
    circulating_supply NUMERIC,
    max_supply NUMERIC,
    cmc_last_updated TIMESTAMP WITH TIME ZONE
);

-- Trigger to lowercase ethereum_address
CREATE OR REPLACE FUNCTION lowercase_ethereum_address() 
RETURNS TRIGGER AS $$
BEGIN
  NEW.ethereum_address = LOWER(NEW.ethereum_address);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_coin_address_lower
BEFORE INSERT OR UPDATE ON coin
FOR EACH ROW EXECUTE FUNCTION lowercase_ethereum_address();

-- Index for case-insensitive lookup (though we force upper)
CREATE UNIQUE INDEX IF NOT EXISTS coin_symbol_idx ON coin (UPPER(symbol));

-- 1.5 COIN FAMILY TABLE
CREATE TABLE IF NOT EXISTS coin_family (
    name VARCHAR(50) NOT NULL,
    symbol VARCHAR(8) NOT NULL REFERENCES coin(symbol),
    PRIMARY KEY (name, symbol)
);

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
    pool_address VARCHAR(42),
    reverted BOOLEAN DEFAULT FALSE,
    UNIQUE(network, protocol, pool_name, fee_tier)
);

CREATE INDEX IF NOT EXISTS idx_lp_pool_address ON liquidity_pool(pool_address);
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

-- 4.5 LIQUIDITY POOL HISTORY TABLE
CREATE TABLE IF NOT EXISTS liquidity_pool_history (
    id SERIAL PRIMARY KEY,
    pool_id INTEGER REFERENCES liquidity_pool(id),
    date DATE NOT NULL,
    tx_count INTEGER DEFAULT 0,
    volume_usd NUMERIC DEFAULT 0,
    tvl_usd NUMERIC DEFAULT 0,
    UNIQUE(pool_id, date)
);
CREATE INDEX IF NOT EXISTS idx_lp_history_date ON liquidity_pool_history(date);
CREATE INDEX IF NOT EXISTS idx_lp_history_pool ON liquidity_pool_history(pool_id);

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
    fee_tier VARCHAR(20),
    network VARCHAR(20) DEFAULT 'Ethereum',
    protocol VARCHAR(50) DEFAULT 'Uniswap V3'
);

CREATE INDEX IF NOT EXISTS idx_swaps_timestamp ON uniswap_v3_swaps(timestamp);
CREATE INDEX IF NOT EXISTS idx_swaps_token0 ON uniswap_v3_swaps(token0_symbol);
CREATE INDEX IF NOT EXISTS idx_swaps_token1 ON uniswap_v3_swaps(token1_symbol);
CREATE INDEX IF NOT EXISTS idx_swaps_network_timestamp ON uniswap_v3_swaps(network, timestamp);

-- 5.1 UNISWAP V4 SWAPS
CREATE TABLE IF NOT EXISTS uniswap_v4_swaps (
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
    fee_tier VARCHAR(20),
    network VARCHAR(20) DEFAULT 'Ethereum',
    protocol VARCHAR(50) DEFAULT 'Uniswap V4'
);

CREATE INDEX IF NOT EXISTS idx_v4_swaps_timestamp ON uniswap_v4_swaps(timestamp);
CREATE INDEX IF NOT EXISTS idx_v4_swaps_token0 ON uniswap_v4_swaps(token0_symbol);
CREATE INDEX IF NOT EXISTS idx_v4_swaps_token1 ON uniswap_v4_swaps(token1_symbol);
CREATE INDEX IF NOT EXISTS idx_v4_swaps_network_timestamp ON uniswap_v4_swaps(network, timestamp);

-- 5.2 UNISWAP V2 SWAPS
CREATE TABLE IF NOT EXISTS uniswap_v2_swaps (
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
    fee_tier VARCHAR(20) DEFAULT '0.30%',
    network VARCHAR(20) DEFAULT 'Ethereum',
    protocol VARCHAR(50) DEFAULT 'Uniswap V2'
);

CREATE INDEX IF NOT EXISTS idx_v2_swaps_timestamp ON uniswap_v2_swaps(timestamp);
CREATE INDEX IF NOT EXISTS idx_v2_swaps_token0 ON uniswap_v2_swaps(token0_symbol);
CREATE INDEX IF NOT EXISTS idx_v2_swaps_token1 ON uniswap_v2_swaps(token1_symbol);
CREATE INDEX IF NOT EXISTS idx_v2_swaps_network_timestamp ON uniswap_v2_swaps(network, timestamp);

-- 5.5 COIN PRICE HISTORY
CREATE TABLE IF NOT EXISTS coin_price_history (
    id SERIAL PRIMARY KEY,
    address VARCHAR(42) NOT NULL REFERENCES coin(ethereum_address),
    symbol VARCHAR(8) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    price NUMERIC NOT NULL,
    UNIQUE(address, timestamp)
);

-- 5.6 LIQUIDITY POOL POSITION EVENTS
CREATE TABLE IF NOT EXISTS liquidity_pool_position_event ( 
    id SERIAL PRIMARY KEY, 
    position_id INTEGER REFERENCES liquidity_pool_position(id), 
    tx_hash VARCHAR(66) NOT NULL, 
    block_number INTEGER NOT NULL, 
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL, 
    event_type VARCHAR(50) NOT NULL, 
    amount0 NUMERIC DEFAULT 0, 
    amount1 NUMERIC DEFAULT 0, 
    amount_usd NUMERIC DEFAULT 0, 
    liquidity_change NUMERIC, 
    tick_lower INTEGER, 
    tick_upper INTEGER, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
    UNIQUE(position_id, tx_hash, event_type) 
); 
CREATE INDEX IF NOT EXISTS idx_lp_event_pos ON liquidity_pool_position_event(position_id); 
CREATE INDEX IF NOT EXISTS idx_lp_event_type ON liquidity_pool_position_event(event_type); 
CREATE INDEX IF NOT EXISTS idx_lp_event_ts ON liquidity_pool_position_event(timestamp);


-- 6. TRIGGERS
CREATE OR REPLACE FUNCTION enforce_uppercase_symbols() 
RETURNS TRIGGER AS $$
BEGIN
  -- Truncate to 8 chars first to respect schema
  NEW.symbol = LEFT(UPPER(NEW.symbol), 8);
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

INSERT INTO coin (symbol, hardness, ethereum_address) VALUES
('ETH', 860, '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'),
('WETH', 860, '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'),
('USDC', 1000, '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'),
('USDT', 990, '0xdac17f958d2ee523a2206206994597c13d831ec7'),
('DAI', 970, '0x6b175474e89094c44da98b954eedeac495271d0f'),
('WBTC', 870, '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599'),
('EURC', 940, '0x1abaea1f7c830f0654c721306e53a20516147924'),
('LINK', 850, '0x514910771af9ca656af840dff83e8264ecf986ca'),
('UNI', 840, '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984'),
('AAVE', 820, '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9'),
('USDS', 980, '0xd75003661288c1c39062eb185cd27962b9a78572'),
('GHO', 950, '0x40d1640030509618f3a3848bdf581d58023c721c'),
('EURI', 920, '0xf23351d4289cf30113a34a81b7e42be005232ba3'),
('STKAAVE', 800, '0x4da27a545c0c5b758a6ba100e3a078a959074b1e'),
('RETH', 860, '0xae78736cd615f374d3085123a210448e74fc6393'),
('STETH', 860, '0xae7ab96520de3a18e5e111b5eaab095312d7fe84'),
('WSTETH', 860, '0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0'),
('SOL', 700, '0xdummy_sol'),
('MSOL', 700, '0xdummy_msol'),
('PENDLE', 600, '0x808507121b80c0546a1d48931130635e169fa121'),
('USDE', 980, '0x4c9edd5852cd14fe7183fdb42c274d2808b04a55'),
('STKGHO', 950, '0xdummy_stkgho'),
('SGHO', 950, '0xdummy_sgho'),
('RLUSD', 980, '0xdummy_rlusd'),
('SUSDS', 980, '0xdummy_susds'),
('EURCV', 940, '0xdummy_eurcv'),
('EURQ', 940, '0xdummy_eurq'),
('PAXG', 850, '0x45804880bdc05151523316d3a01ff660eacc9292'),
('XAUT', 850, '0x68749665e53399066df52a092dd62128b1bf1e6f'),
('BTC', 900, '0xdummy_btc'),
('CBBTC', 900, '0xcbb7c919d3639a04f981e285d03837da2ee418d1'),
('STAAVE', 800, '0xdummy_staave'),
('CLAAVE', 800, '0xdummy_claave'),
('MIM', 970, '0x99d1ed205117f739c49110052e42337777777777'),
('ENA', 500, '0x57e114b691db790c35207b2e685d4a43181e6061')
ON CONFLICT (symbol) DO UPDATE SET ethereum_address = EXCLUDED.ethereum_address;

-- Populate coin_family with initial multi-coin families
-- (Handled by a temporary array for simplicity in init_db or manual insertion)
INSERT INTO coin_family (name, symbol) VALUES
('USD', 'USDC'), ('USD', 'USDT'), ('USD', 'USDS'), ('USD', 'DAI'), ('USD', 'USDE'), ('USD', 'GHO'), ('USD', 'STKGHO'), ('USD', 'SGHO'), ('USD', 'RLUSD'), ('USD', 'SUSDS'),
('EUR', 'EURC'), ('EUR', 'EURCV'), ('EUR', 'EURI'), ('EUR', 'EURQ'),
('GOLD', 'PAXG'), ('GOLD', 'XAUT'),
('BTC', 'BTC'), ('BTC', 'WBTC'), ('BTC', 'CBBTC'),
('ETH', 'ETH'), ('ETH', 'WETH'), ('ETH', 'STETH'), ('ETH', 'WSTETH'),
('AAVE', 'AAVE'), ('AAVE', 'STAAVE'), ('AAVE', 'STKAAVE'), ('AAVE', 'CLAAVE')
ON CONFLICT DO NOTHING;

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
    pool.fee_tier,
    s.coin0_claimed_amount,
    s.coin1_claimed_amount
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id;
