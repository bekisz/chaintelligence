-- ============================================================================
-- SQL Migration: Normalize swaps table with pool_id foreign key
-- ============================================================================

BEGIN;

-- 1. Add pool_id column
ALTER TABLE swaps ADD COLUMN IF NOT EXISTS pool_id INT;

-- 2. Populate pool_id (in two optimized steps to avoid slow OR join conditions)
UPDATE swaps s
SET pool_id = lp.id
FROM liquidity_pool lp
WHERE s.chain_id = lp.chain_id
  AND s.protocol_id = lp.protocol_id
  AND s.t0_coin_id = lp.coin0_id AND s.t1_coin_id = lp.coin1_id
  AND (s.fee_bps = lp.fee_bps OR (s.fee_bps IS NULL AND lp.fee_bps IS NULL));

UPDATE swaps s
SET pool_id = lp.id
FROM liquidity_pool lp
WHERE s.pool_id IS NULL
  AND s.chain_id = lp.chain_id
  AND s.protocol_id = lp.protocol_id
  AND s.t0_coin_id = lp.coin1_id AND s.t1_coin_id = lp.coin0_id
  AND (s.fee_bps = lp.fee_bps OR (s.fee_bps IS NULL AND lp.fee_bps IS NULL));

-- 3. Delete orphan swaps that do not belong to any tracked liquidity pool
DELETE FROM swaps WHERE pool_id IS NULL;

-- 4. Enforce not null constraint on pool_id
ALTER TABLE swaps ALTER COLUMN pool_id SET NOT NULL;

-- 4. Add foreign key constraint to parent table
-- Note: PostgreSQL supports adding FK constraints to partitioned tables in version 12+.
ALTER TABLE swaps ADD CONSTRAINT fk_swaps_pool_id FOREIGN KEY (pool_id) REFERENCES liquidity_pool(id);

-- 5. Drop redundant columns (drop dependent views first)
DROP VIEW IF EXISTS uniswap_v3_swaps CASCADE;
DROP VIEW IF EXISTS uniswap_v2_swaps CASCADE;
DROP VIEW IF EXISTS uniswap_v4_swaps CASCADE;

ALTER TABLE swaps DROP COLUMN IF EXISTS t0_coin_id;
ALTER TABLE swaps DROP COLUMN IF EXISTS t1_coin_id;
ALTER TABLE swaps DROP COLUMN IF EXISTS fee_bps;
ALTER TABLE swaps DROP COLUMN IF EXISTS chain_id;
ALTER TABLE swaps DROP COLUMN IF EXISTS protocol_id;

-- 6. Rebuild indexes
DROP INDEX IF EXISTS idx_swaps_coin1_0_ts;
DROP INDEX IF EXISTS idx_swaps_coin_pair_ts;

CREATE INDEX idx_swaps_pool_id_ts ON swaps (pool_id, ts) INCLUDE (amount_usd, amount0, amount1, fee_display);

-- 7. Recreate compatibility views
CREATE OR REPLACE VIEW uniswap_v3_swaps AS
SELECT 
    (s.tx_hash || '#' || s.log_index) AS id,
    s.tx_hash AS transaction,
    s.ts AS timestamp,
    c0.symbol AS token0_symbol,
    cc0.contract_address AS token0_address,
    c1.symbol AS token1_symbol,
    cc1.contract_address AS token1_address,
    s.amount0 AS amount0,
    s.amount1 AS amount1,
    s.amount_usd AS "amountUSD",
    s.fee_display AS "feeTier",
    ch.name AS network,
    pr.name AS protocol
FROM swaps s
JOIN liquidity_pool pool ON s.pool_id = pool.id
JOIN chain ch ON pool.chain_id = ch.id
JOIN protocol pr ON pool.protocol_id = pr.id
JOIN coin c0 ON pool.coin0_id = c0.coin_id
JOIN coin c1 ON pool.coin1_id = c1.coin_id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = pool.coin0_id AND cc0.chain_id = pool.chain_id
LEFT JOIN coin_contract cc1 ON cc1.coin_id = pool.coin1_id AND cc1.chain_id = pool.chain_id
WHERE pr.name IN ('Uniswap V3', 'PancakeSwap V3', 'Aerodrome');

CREATE OR REPLACE VIEW uniswap_v2_swaps AS
SELECT 
    ('v2-' || s.tx_hash || '-' || s.log_index) AS id,
    s.tx_hash AS transaction,
    s.ts AS timestamp,
    c0.symbol AS token0_symbol,
    cc0.contract_address AS token0_address,
    c1.symbol AS token1_symbol,
    cc1.contract_address AS token1_address,
    s.amount0 AS amount0,
    s.amount1 AS amount1,
    s.amount_usd AS "amountUSD",
    s.fee_display AS "feeTier",
    ch.name AS network,
    pr.name AS protocol
FROM swaps s
JOIN liquidity_pool pool ON s.pool_id = pool.id
JOIN chain ch ON pool.chain_id = ch.id
JOIN protocol pr ON pool.protocol_id = pr.id
JOIN coin c0 ON pool.coin0_id = c0.coin_id
JOIN coin c1 ON pool.coin1_id = c1.coin_id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = pool.coin0_id AND cc0.chain_id = pool.chain_id
LEFT JOIN coin_contract cc1 ON cc1.coin_id = pool.coin1_id AND cc1.chain_id = pool.chain_id
WHERE pr.name = 'Uniswap V2';

CREATE OR REPLACE VIEW uniswap_v4_swaps AS
SELECT 
    (s.tx_hash || '-' || s.log_index) AS id,
    s.tx_hash AS transaction,
    s.ts AS timestamp,
    c0.symbol AS token0_symbol,
    cc0.contract_address AS token0_address,
    c1.symbol AS token1_symbol,
    cc1.contract_address AS token1_address,
    s.amount0 AS amount0,
    s.amount1 AS amount1,
    s.amount_usd AS "amountUSD",
    s.fee_display AS "feeTier",
    ch.name AS network,
    pr.name AS protocol
FROM swaps s
JOIN liquidity_pool pool ON s.pool_id = pool.id
JOIN chain ch ON pool.chain_id = ch.id
JOIN protocol pr ON pool.protocol_id = pr.id
JOIN coin c0 ON pool.coin0_id = c0.coin_id
JOIN coin c1 ON pool.coin1_id = c1.coin_id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = pool.coin0_id AND cc0.chain_id = pool.chain_id
LEFT JOIN coin_contract cc1 ON cc1.coin_id = pool.coin1_id AND cc1.chain_id = pool.chain_id
WHERE pr.name IN ('Uniswap V4', 'PancakeSwap V4');

COMMIT;
