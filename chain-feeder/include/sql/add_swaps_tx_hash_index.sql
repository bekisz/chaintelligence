-- ============================================================================
-- Add an index on swaps(tx_hash) for per-transaction route reconstruction.
--
-- The swap-distribution "Hops" grouping and split detection need to pull, for
-- each matching swap, ALL legs of its transaction (including intermediate
-- tokens not on the queried pair). The covering (pool_id, ts) and (t0_coin_id,
-- t1_coin_id, ts) indexes cannot serve a lookup keyed only by tx_hash, so a
-- dedicated index is required to avoid a per-partition scan.
--
-- Runs on the declaratively partitioned `swaps` table. CREATE INDEX on the
-- parent automatically propagates the index to every existing partition and to
-- any partition attached later.
--
-- Run with:
--   psql "$DATA_WAREHOUSE_DB" -f add_swaps_tx_hash_index.sql
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_swaps_tx_hash ON swaps (tx_hash)
    INCLUDE (pool_id, amount0, amount1, amount_usd);

-- ============================================================================
-- Done. No ANALYZE needed for a fresh btree over a just-populated column, but
-- it costs nothing and stops the planner from guessing on first use.
-- ============================================================================

ANALYZE swaps;