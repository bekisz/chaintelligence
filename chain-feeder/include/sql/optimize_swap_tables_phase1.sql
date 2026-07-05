-- ============================================================================
-- Phase 1: Swap table optimization
-- Target: uniswap_v3_swaps, uniswap_v4_swaps, uniswap_v2_swaps
--
-- Changes:
--   1. Drop B-tree indexes redundant with covering & BRIN indexes:
--      - token0 (covered by symbol_ts_cover leading column)
--      - timestamp (redundant with BRIN for range scans)
--   2. NUMERIC(amount0, amount1, amount_usd) → DOUBLE PRECISION
--      (~15 bytes/row saved, faster aggregation, no code change needed)
--   3. V2 VARCHAR alignment to match V3/V4
--   4. ANALYZE tables
--
-- The BRIN indexes on (network, timestamp) were already created by the
-- replace_covering_indexes_with_brin.sql migration; only the redundant
-- B-tree indexes and column type changes remain.
--
-- Run in a maintenance window (ALTER COLUMN TYPE takes ACCESS EXCLUSIVE
-- lock and rewrites the heap — ~a couple minutes for 33 GB v3 table):
--   docker exec -i chaintelligence-postgres-1 psql -U airflow -d chaintelligence \
--     < chain-feeder/include/sql/optimize_swap_tables_phase1.sql
--
-- Or from inside the container:
--   psql -U airflow -d chaintelligence -f /include/sql/optimize_swap_tables_phase1.sql
-- ============================================================================

BEGIN;

-- ------------------------------------------------------------------
-- 1. Drop redundant B-tree indexes
--    idx_*_token0 is covered by the leading column of idx_*_symbol_ts_cover
--    idx_*_timestamp is redundant with BRIN (network, timestamp)
--    Drop CONCURRENTLY is not needed inside a transaction block per se,
--    but we have only metadata-level locks here so this is safe.
-- ------------------------------------------------------------------
DROP INDEX IF EXISTS idx_swaps_token0;
DROP INDEX IF EXISTS idx_swaps_timestamp;

DROP INDEX IF EXISTS idx_v4_swaps_token0;
DROP INDEX IF EXISTS idx_v4_swaps_timestamp;

DROP INDEX IF EXISTS idx_v2_swaps_token0;
DROP INDEX IF EXISTS idx_v2_swaps_timestamp;

-- ------------------------------------------------------------------
-- 2. V2 VARCHAR alignment with V3/V4 (100 instead of 255)
--    Only necessary if the BRIN migration wasn't applied to V2.
--    V2 table is 133 MB — metadata+rewrite is sub-second.
-- ------------------------------------------------------------------
ALTER TABLE uniswap_v2_swaps
    ALTER COLUMN token0_symbol TYPE VARCHAR(100),
    ALTER COLUMN token1_symbol TYPE VARCHAR(100);

-- ------------------------------------------------------------------
-- 3. NUMERIC columns → DOUBLE PRECISION
--    Each ALTER TABLE rewrites the heap once (all three column type
--    changes happen in a single table rewrite).
--
--    Insert path (DAGs): already sends Python floats via %s placeholders
--    Read path (postgres_fetcher): already converts with float()
--    → No application code changes needed.
-- ------------------------------------------------------------------
ALTER TABLE uniswap_v3_swaps
    ALTER COLUMN amount0 TYPE DOUBLE PRECISION USING amount0::double precision,
    ALTER COLUMN amount1 TYPE DOUBLE PRECISION USING amount1::double precision,
    ALTER COLUMN amount_usd TYPE DOUBLE PRECISION USING amount_usd::double precision;

ALTER TABLE uniswap_v4_swaps
    ALTER COLUMN amount0 TYPE DOUBLE PRECISION USING amount0::double precision,
    ALTER COLUMN amount1 TYPE DOUBLE PRECISION USING amount1::double precision,
    ALTER COLUMN amount_usd TYPE DOUBLE PRECISION USING amount_usd::double precision;

ALTER TABLE uniswap_v2_swaps
    ALTER COLUMN amount0 TYPE DOUBLE PRECISION USING amount0::double precision,
    ALTER COLUMN amount1 TYPE DOUBLE PRECISION USING amount1::double precision,
    ALTER COLUMN amount_usd TYPE DOUBLE PRECISION USING amount_usd::double precision;

COMMIT;

-- ------------------------------------------------------------------
-- 4. Refresh planner statistics
-- ------------------------------------------------------------------
ANALYZE uniswap_v3_swaps;
ANALYZE uniswap_v4_swaps;
ANALYZE uniswap_v2_swaps;