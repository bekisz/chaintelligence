-- ============================================================================
-- SQL Migration: Drop fee_display column from swaps table
-- ============================================================================

BEGIN;

-- 1. Recreate compatibility views (drop dependent views first)
DROP VIEW IF EXISTS uniswap_v3_swaps CASCADE;
DROP VIEW IF EXISTS uniswap_v2_swaps CASCADE;
DROP VIEW IF EXISTS uniswap_v4_swaps CASCADE;

-- 2. Drop redundant column
ALTER TABLE swaps DROP COLUMN IF EXISTS fee_display;

-- 3. Rebuild indexes (remove fee_display from include clause)
DROP INDEX IF EXISTS idx_swaps_pool_id_ts;
CREATE INDEX idx_swaps_pool_id_ts ON swaps (pool_id, ts) INCLUDE (amount_usd, amount0, amount1);

-- 4. Recreate compatibility views with dynamic fee_tier calculation
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
    CASE 
        WHEN pool.fee_bps IS NULL THEN 'Dynamic' 
        ELSE (pool.fee_bps / 100.0)::text || '%' 
    END AS "feeTier",
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
    CASE 
        WHEN pool.fee_bps IS NULL THEN 'Dynamic' 
        ELSE (pool.fee_bps / 100.0)::text || '%' 
    END AS "feeTier",
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
    CASE 
        WHEN pool.fee_bps IS NULL THEN 'Dynamic' 
        ELSE (pool.fee_bps / 100.0)::text || '%' 
    END AS "feeTier",
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
