-- ============================================================================
-- Phase 2: Create indexes on the unified `swaps` table
--
-- Run AFTER migrate_swaps_data.sql:
--   psql "$DATA_WAREHOUSE_DB" -f create_swaps_indexes.sql
-- ============================================================================

-- ============================================================================
-- Covering index for routing queries: (t0_coin_id, t1_coin_id, ts)
-- The leading column serves the token-pair filter, ts serves the range filter.
-- INCLUDE columns avoid heap fetches for the most-common query columns.
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_swaps_coin_pair_ts
    ON swaps (t0_coin_id, t1_coin_id, ts)
    INCLUDE (amount_usd, network, protocol, fee_bps, fee_display);

-- ============================================================================
-- Mirror index for the reverse token pair: (t1_coin_id, t0_coin_id, ts)
-- Covers queries where the user's "start_token" is in token1 position.
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_swaps_coin1_0_ts
    ON swaps (t1_coin_id, t0_coin_id, ts)
    INCLUDE (amount_usd, network, protocol, fee_bps, fee_display);

-- ============================================================================
-- BRIN indexes on each partition for network+ts range scans
-- These are tiny (~50 KB per partition) vs B-tree equivalents (~500 MB)
-- ============================================================================

CREATE INDEX IF NOT EXISTS swaps_2025_06_brin ON swaps_2025_06 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2025_07_brin ON swaps_2025_07 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2025_08_brin ON swaps_2025_08 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2025_09_brin ON swaps_2025_09 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2025_10_brin ON swaps_2025_10 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2025_11_brin ON swaps_2025_11 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2025_12_brin ON swaps_2025_12 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_01_brin ON swaps_2026_01 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_02_brin ON swaps_2026_02 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_03_brin ON swaps_2026_03 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_04_brin ON swaps_2026_04 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_05_brin ON swaps_2026_05 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_06_brin ON swaps_2026_06 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_07_brin ON swaps_2026_07 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_08_brin ON swaps_2026_08 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_09_brin ON swaps_2026_09 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_10_brin ON swaps_2026_10 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_11_brin ON swaps_2026_11 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_2026_12_brin ON swaps_2026_12 USING BRIN (network, ts) WITH (pages_per_range = 32);
CREATE INDEX IF NOT EXISTS swaps_default_brin ON swaps_default USING BRIN (network, ts) WITH (pages_per_range = 32);

-- ============================================================================
-- Refresh planner statistics
-- ============================================================================

ANALYZE swaps;

-- ============================================================================
-- Done. Now update the application code to read from/write to `swaps`.
-- ============================================================================