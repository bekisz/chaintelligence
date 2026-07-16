-- ============================================================================
-- SQL Migration: Optimize numeric types to DOUBLE PRECISION
-- ============================================================================

BEGIN;

-- 1. Optimize `liquidity_pool_history` Table
ALTER TABLE liquidity_pool_history ALTER COLUMN volume_usd TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_history ALTER COLUMN tvl_usd TYPE DOUBLE PRECISION;

-- 2. Optimize `liquidity_pool_position_snapshot` Table
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN balance_usd TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin0_amount TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin1_amount TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin0_claimable_amount TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin1_claimable_amount TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin0_claimed_amount TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin1_claimed_amount TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN current_price TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin0_usd TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin1_usd TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin0_claimable_usd TYPE DOUBLE PRECISION;
ALTER TABLE liquidity_pool_position_snapshot ALTER COLUMN coin1_claimable_usd TYPE DOUBLE PRECISION;

COMMIT;
