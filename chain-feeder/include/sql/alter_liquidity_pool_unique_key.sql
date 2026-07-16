-- ============================================================================
-- SQL Migration: Refactor unique constraint to support duplicate pools with different hooks
-- ============================================================================

BEGIN;

-- 1. Drop old constraint
ALTER TABLE liquidity_pool DROP CONSTRAINT IF EXISTS liquidity_pool_chain_protocol_name_fee_bps_key;

-- 2. Create coalesced unique index
CREATE UNIQUE INDEX IF NOT EXISTS idx_liquidity_pool_canonical 
ON liquidity_pool (chain_id, protocol_id, pool_name, fee_bps, COALESCE(pool_id, ''));

COMMIT;
