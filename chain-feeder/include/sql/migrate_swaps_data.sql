-- ============================================================================
-- Phase 2: Migrate swap data from old tables to unified `swaps` table
--
-- Only swaps where BOTH tokens are in the coin table are migrated.
-- Others are dropped (the user confirmed this is acceptable).
--
-- Run AFTER create_swaps_table.sql:
--   psql "$DATA_WAREHOUSE_DB" -f migrate_swaps_data.sql
-- ============================================================================

-- ============================================================================
-- PART 1: Migrate from uniswap_v3_swaps (25.3M rows)
-- ID formats:
--   - {tx_hash}#{log_index}  (Uniswap V3 Ethereum/Arbitrum/Base)
--   - {tx_hash}{hex_suffix}  (Uniswap V3 BNB, PancakeSwap V3 BNB — hex suffix
--                              is little-endian uint32 encoding log_index)
-- Use the existing tx_hash column (correctly populated by DAGs) directly,
-- and extract log_index from the id suffix.
-- ============================================================================

INSERT INTO swaps (tx_hash, log_index, ts, network, protocol, t0_coin_id, t1_coin_id,
                   amount0, amount1, amount_usd, fee_bps, fee_display)
SELECT
    s.tx_hash,
    CASE
        WHEN s.id LIKE '%#%' THEN split_part(s.id, '#', 2)::int
        WHEN s.id LIKE '%-%' AND s.id ~ '^0x[0-9a-f]+-[0-9]+$' THEN
            split_part(s.id, '-', 2)::int
        WHEN LENGTH(s.id) > LENGTH(s.tx_hash) THEN
            -- Little-endian uint32 from hex suffix after tx_hash
            ('x' ||
                SUBSTRING(s.id FROM LENGTH(s.tx_hash) + 7 FOR 2) ||
                SUBSTRING(s.id FROM LENGTH(s.tx_hash) + 5 FOR 2) ||
                SUBSTRING(s.id FROM LENGTH(s.tx_hash) + 3 FOR 2) ||
                SUBSTRING(s.id FROM LENGTH(s.tx_hash) + 1 FOR 2)
            )::bit(32)::int
        ELSE 0
    END AS log_index,
    s.timestamp,
    s.network,
    s.protocol,
    c0.coin_id,
    c1.coin_id,
    s.amount0,
    s.amount1,
    s.amount_usd,
    CASE
        WHEN s.fee_tier = 'Dynamic' THEN NULL
        WHEN s.fee_tier ~ '^[\d.]+%$' THEN
            REGEXP_REPLACE(s.fee_tier, '%$', '')::double precision * 100
        ELSE NULL
    END AS fee_bps,
    s.fee_tier AS fee_display
FROM uniswap_v3_swaps s
JOIN coin c0 ON UPPER(s.token0_symbol) = UPPER(c0.symbol)
JOIN coin c1 ON UPPER(s.token1_symbol) = UPPER(c1.symbol)
ON CONFLICT (ts, tx_hash, log_index) DO NOTHING;

-- ============================================================================
-- PART 2: Migrate from uniswap_v4_swaps (8.5M rows)
-- ID format: {tx_hash}-{log_index}
-- ============================================================================

INSERT INTO swaps (tx_hash, log_index, ts, network, protocol, t0_coin_id, t1_coin_id,
                   amount0, amount1, amount_usd, fee_bps, fee_display)
SELECT
    split_part(s.id, '-', 1) AS tx_hash,
    split_part(s.id, '-', 2)::int AS log_index,
    s.timestamp,
    s.network,
    s.protocol,
    c0.coin_id,
    c1.coin_id,
    s.amount0,
    s.amount1,
    s.amount_usd,
    CASE
        WHEN s.fee_tier = 'Dynamic' THEN NULL
        WHEN s.fee_tier ~ '^[\d.]+%$' THEN
            REGEXP_REPLACE(s.fee_tier, '%$', '')::double precision * 100
        ELSE NULL
    END AS fee_bps,
    s.fee_tier AS fee_display
FROM uniswap_v4_swaps s
JOIN coin c0 ON UPPER(s.token0_symbol) = UPPER(c0.symbol)
JOIN coin c1 ON UPPER(s.token1_symbol) = UPPER(c1.symbol)
ON CONFLICT (ts, tx_hash, log_index) DO NOTHING;

-- ============================================================================
-- PART 3: Migrate from uniswap_v2_swaps (180K rows)
-- ID format: v2-{tx_hash}-{log_index}
-- ============================================================================

INSERT INTO swaps (tx_hash, log_index, ts, network, protocol, t0_coin_id, t1_coin_id,
                   amount0, amount1, amount_usd, fee_bps, fee_display)
SELECT
    split_part(SUBSTRING(s.id FROM 4), '-', 1) AS tx_hash,
    split_part(SUBSTRING(s.id FROM 4), '-', 2)::int AS log_index,
    s.timestamp,
    s.network,
    s.protocol,
    c0.coin_id,
    c1.coin_id,
    s.amount0,
    s.amount1,
    s.amount_usd,
    CASE
        WHEN s.fee_tier = 'Dynamic' THEN NULL
        WHEN s.fee_tier ~ '^[\d.]+%$' THEN
            REGEXP_REPLACE(s.fee_tier, '%$', '')::double precision * 100
        ELSE NULL
    END AS fee_bps,
    s.fee_tier AS fee_display
FROM uniswap_v2_swaps s
JOIN coin c0 ON UPPER(s.token0_symbol) = UPPER(c0.symbol)
JOIN coin c1 ON UPPER(s.token1_symbol) = UPPER(c1.symbol)
ON CONFLICT (ts, tx_hash, log_index) DO NOTHING;

-- ============================================================================
-- Done. Run create_swaps_indexes.sql next to create indexes per partition.
-- ============================================================================