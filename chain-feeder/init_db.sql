-- Create the data warehouse database
CREATE DATABASE chaintelligence;

-- Switch to the new database to create tables
\c chaintelligence;

CREATE TABLE IF NOT EXISTS lp_snapshots (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    address VARCHAR(42),
    position_key VARCHAR(100),
    protocol VARCHAR(50),
    network VARCHAR(50),
    position_label VARCHAR(255),
    balance_usd NUMERIC,
    assets JSONB,
    unclaimed JSONB,
    images JSONB
);

-- Table for raw Uniswap V3 swap events
CREATE TABLE IF NOT EXISTS uniswap_v3_swaps (
    id VARCHAR(255) PRIMARY KEY, -- The Graph swap ID (tx_hash#log_index)
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

-- View to flatten the assets and unclaimed JSONB objects from lp_snapshots
CREATE OR REPLACE VIEW v_lp_snapshot_details AS
SELECT 
    id AS snapshot_id,
    timestamp,
    address,
    protocol,
    network,
    position_label,
    balance_usd AS total_position_balance_usd,
    'asset' AS item_type,
    (asset->>'symbol') AS symbol,
    (asset->>'balance')::NUMERIC AS balance,
    (asset->>'balanceUSD')::NUMERIC AS balance_usd,
    (asset->>'price')::NUMERIC AS price
FROM lp_snapshots, 
     jsonb_array_elements(assets) AS asset
UNION ALL
SELECT 
    id AS snapshot_id,
    timestamp,
    address,
    protocol,
    network,
    position_label,
    balance_usd AS total_position_balance_usd,
    'unclaimed' AS item_type,
    (uncl->>'symbol') AS symbol,
    (uncl->>'balance')::NUMERIC AS balance,
    (uncl->>'balanceUSD')::NUMERIC AS balance_usd,
    (uncl->>'price')::NUMERIC AS price
FROM lp_snapshots, 
     jsonb_array_elements(unclaimed) AS uncl;

-- View to provide a one-row-per-position summary of LP snapshots
CREATE OR REPLACE VIEW v_lp_snapshots_summary AS
SELECT 
    id,
    timestamp,
    address,
    position_key,
    protocol,
    network,
    position_label,
    balance_usd,
    -- Flattened assets (assuming up to 2 for common pools)
    assets->0->>'symbol' as asset0_symbol,
    (assets->0->>'balance')::NUMERIC as asset0_amount,
    (assets->0->>'balanceUSD')::NUMERIC as asset0_usd,
    assets->1->>'symbol' as asset1_symbol,
    (assets->1->>'balance')::NUMERIC as asset1_amount,
    (assets->1->>'balanceUSD')::NUMERIC as asset1_usd,
    -- Flattened rewards
    unclaimed->0->>'symbol' as reward0_symbol,
    (unclaimed->0->>'balance')::NUMERIC as reward0_amount,
    (unclaimed->0->>'balanceUSD')::NUMERIC as reward0_usd,
    unclaimed->1->>'symbol' as reward1_symbol,
    (unclaimed->1->>'balance')::NUMERIC as reward1_amount,
    (unclaimed->1->>'balanceUSD')::NUMERIC as reward1_usd,
    -- Keep original JSON columns for app compatibility
    assets,
    unclaimed,
    images,
    -- Extract total rewards for sorting/summary
    COALESCE((SELECT SUM((u->>'balanceUSD')::NUMERIC) FROM jsonb_array_elements(unclaimed) u), 0) as total_unclaimed_usd
FROM lp_snapshots;

