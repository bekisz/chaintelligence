-- ============================================================================
-- SQL Migration: Normalize pool fields (fee_tier and pool_address)
-- ============================================================================

BEGIN;

-- 1. Shrink pool_address to VARCHAR(100) (allows EVM V2/V3 42-char addresses and V4 88-char compound IDs)
ALTER TABLE liquidity_pool ALTER COLUMN pool_address TYPE VARCHAR(100);

-- 2. Expand fee_tier to VARCHAR(20) to prevent truncation of custom percentage strings
ALTER TABLE liquidity_pool ALTER COLUMN fee_tier TYPE VARCHAR(20);

-- 3. Add fee_bps column to liquidity_pool
ALTER TABLE liquidity_pool ADD COLUMN IF NOT EXISTS fee_bps DOUBLE PRECISION;

-- 4. Populate fee_bps based on fee_tier string
UPDATE liquidity_pool
SET fee_bps = CASE
    WHEN fee_tier IS NULL OR LOWER(fee_tier) = 'dynamic' THEN NULL
    WHEN POSITION('%' IN fee_tier) > 0 THEN (REPLACE(fee_tier, '%', '')::DOUBLE PRECISION) * 100.0
    WHEN fee_tier ~ '^[0-9]+$' THEN (fee_tier::DOUBLE PRECISION) / 100.0
    ELSE NULL
END;

COMMIT;
