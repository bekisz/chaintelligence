-- ============================================================================
-- Migration: replace large covering B-tree indexes with BRIN indexes
-- Target tables: uniswap_v3_swaps, uniswap_v4_swaps, uniswap_v2_swaps
--
-- The swap tables are append-only time-series data. BRIN indexes on
-- (network, timestamp) are ~50-100 KB each vs 3-4 GB for the covering
-- B-tree variants, and they prune 99.7%+ of pages for typical 2-day queries.
--
-- Additionally, V2 token_symbol columns are aligned with V3/V4 (VARCHAR(100)).
--
-- All index operations use CONCURRENTLY so Airflow DAGs can keep writing.
-- Run in psql autocommit mode (CONCURRENTLY requires one statement per txn):
--   psql "$DATA_WAREHOUSE_DB" -f replace_covering_indexes_with_brin.sql
-- ============================================================================

-- 1. Drop unused stablecoin partial indexes (0 scans ever, ~236 MB)
DROP INDEX CONCURRENTLY IF EXISTS idx_swaps_stablecoin_cover;
DROP INDEX CONCURRENTLY IF EXISTS idx_v4_swaps_stablecoin_cover;

-- 2. Create BRIN indexes for V3
-- Single multi-column BRIN serves both network-filtered and network-agnostic
-- queries — BRIN prunes on any column independently per page range.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_swaps_network_timestamp_brin
    ON uniswap_v3_swaps USING BRIN (network, timestamp)
    WITH (pages_per_range = 32);

-- 3. Create BRIN indexes for V4
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_v4_swaps_network_timestamp_brin
    ON uniswap_v4_swaps USING BRIN (network, timestamp)
    WITH (pages_per_range = 32);

-- 4. Drop the now-redundant covering B-tree indexes
-- These were 3-4 GB each and are replaced by the ~50-100 KB BRIN indexes above.
DROP INDEX CONCURRENTLY IF EXISTS idx_swaps_net_ts_cover;
DROP INDEX CONCURRENTLY IF EXISTS idx_swaps_ts_cover;
DROP INDEX CONCURRENTLY IF EXISTS idx_v4_swaps_net_ts_cover;
DROP INDEX CONCURRENTLY IF EXISTS idx_v4_swaps_ts_cover;

-- 5. Align V2 column lengths with V3/V4 (VARCHAR(255) -> VARCHAR(100))
-- V2 table is currently empty, so this is a fast metadata-only operation.
ALTER TABLE uniswap_v2_swaps
    ALTER COLUMN token0_symbol TYPE VARCHAR(100),
    ALTER COLUMN token1_symbol TYPE VARCHAR(100);

-- 6. Add V2 BRIN + covering symbol index (for future data)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_v2_swaps_network_timestamp_brin
    ON uniswap_v2_swaps USING BRIN (network, timestamp)
    WITH (pages_per_range = 32);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_v2_swaps_symbol_ts_cover
    ON uniswap_v2_swaps (token0_symbol, token1_symbol, timestamp)
    INCLUDE (amount_usd, network, tx_hash, fee_tier, protocol);

-- 7. Refresh planner statistics so the planner is aware of the new indexes
ANALYZE uniswap_v3_swaps;
ANALYZE uniswap_v4_swaps;
ANALYZE uniswap_v2_swaps;

-- ============================================================================
-- End of migration
-- ============================================================================