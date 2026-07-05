-- Migration: Add Uniswap V2 swaps table
-- Run this against the chaintelligence database after init_db.sql has been applied.

CREATE TABLE IF NOT EXISTS uniswap_v2_swaps (
    id VARCHAR(255) PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    tx_hash VARCHAR(66) NOT NULL,
    token0_address VARCHAR(42) NOT NULL,
    token1_address VARCHAR(42) NOT NULL,
    token0_symbol VARCHAR(255),
    token1_symbol VARCHAR(255),
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