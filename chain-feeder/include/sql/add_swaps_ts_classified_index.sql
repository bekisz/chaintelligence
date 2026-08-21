-- ============================================================================
-- Partial index on swaps(ts) for classified swaps (route_id IS NOT NULL)
--
-- The O&D goal-state base-floor coverage checks (include/od_retention.py) run:
--
--     SELECT DISTINCT s.ts::date FROM swaps s
--     WHERE s.route_id IS NOT NULL AND s.ts >= ... AND s.ts < ...
--
-- There is no ts-only index, so these queries full-scan the partitioned swaps
-- table (several seconds to minutes when the DB is loaded). The primary key is
-- (ts, tx_hash, log_index), which leads with ts but does not cover route_id, so
-- the route_id IS NOT NULL filter still forces heap lookups.
--
-- This partial index restricts to classified swaps so the coverage query is
-- answered with an index-only scan. Created on the partitioned parent; the
-- index is built recursively on every existing partition and is auto-created
-- on any future partition (CREATE TABLE ... PARTITION OF inherits it).
--
-- NOTE: CONCURRENTLY is not permitted on a partitioned table, so this is a
-- blocking CREATE INDEX. Run during a low-ingestion window.
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_swaps_ts_classified
    ON swaps (ts)
    WHERE route_id IS NOT NULL;
