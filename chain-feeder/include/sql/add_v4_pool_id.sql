-- Add pool_id column to liquidity_pool for Uniswap V4 pool IDs (bytes32)
-- V4 pools are identified by poolId = keccak256(abi.encode(PoolKey)) rather
-- than a deployed contract address, so we store the bytes32 hex here.
-- This column is NULL for V2/V3 pools (which use pool_address instead).

ALTER TABLE liquidity_pool ADD COLUMN IF NOT EXISTS pool_id VARCHAR(66);

CREATE INDEX IF NOT EXISTS idx_lp_pool_id ON liquidity_pool(pool_id);