-- ============================================================================
-- Create compatibility views for legacy table queries
--
-- This maps the new unified `swaps` table to the old V2, V3, and V4 table schemas.
-- This allows legacy ETL DAGs to run without queries failing due to missing tables.
-- ============================================================================

-- 1. Uniswap V3 Compatibility View
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
    s.network AS network,
    s.protocol AS protocol
FROM swaps s
JOIN coin c0 ON s.t0_coin_id = c0.coin_id
JOIN coin c1 ON s.t1_coin_id = c1.coin_id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = s.t0_coin_id AND LOWER(cc0.chain) = CASE WHEN s.network = 'BNB' THEN 'bsc' ELSE LOWER(s.network) END
LEFT JOIN coin_contract cc1 ON cc1.coin_id = s.t1_coin_id AND LOWER(cc1.chain) = CASE WHEN s.network = 'BNB' THEN 'bsc' ELSE LOWER(s.network) END
WHERE s.protocol = 'Uniswap V3' OR s.protocol = 'PancakeSwap V3' OR s.protocol = 'Aerodrome';

-- 2. Uniswap V2 Compatibility View
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
    s.network AS network,
    s.protocol AS protocol
FROM swaps s
JOIN coin c0 ON s.t0_coin_id = c0.coin_id
JOIN coin c1 ON s.t1_coin_id = c1.coin_id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = s.t0_coin_id AND LOWER(cc0.chain) = CASE WHEN s.network = 'BNB' THEN 'bsc' ELSE LOWER(s.network) END
LEFT JOIN coin_contract cc1 ON cc1.coin_id = s.t1_coin_id AND LOWER(cc1.chain) = CASE WHEN s.network = 'BNB' THEN 'bsc' ELSE LOWER(s.network) END
WHERE s.protocol = 'Uniswap V2';

-- 3. Uniswap V4 Compatibility View
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
    s.network AS network,
    s.protocol AS protocol
FROM swaps s
JOIN coin c0 ON s.t0_coin_id = c0.coin_id
JOIN coin c1 ON s.t1_coin_id = c1.coin_id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = s.t0_coin_id AND LOWER(cc0.chain) = CASE WHEN s.network = 'BNB' THEN 'bsc' ELSE LOWER(s.network) END
LEFT JOIN coin_contract cc1 ON cc1.coin_id = s.t1_coin_id AND LOWER(cc1.chain) = CASE WHEN s.network = 'BNB' THEN 'bsc' ELSE LOWER(s.network) END
WHERE s.protocol = 'Uniswap V4' OR s.protocol = 'PancakeSwap V4';
