-- Add per-route daily fee revenue so /api/swap-time-series can serve fees
-- without reading the raw swaps tables.
--
-- The daily rollups (recompute_daily_stats / DAG route_daily_stats_rollup)
-- populate fees_usd on subsequent runs; backfill with:
--   python chain-feeder/include/scripts/backfill_route_tables.py --days <range>  (recompute path)
-- or simply rerun the route_daily_stats_rollup DAG for recent days.

ALTER TABLE route_daily_stats
    ADD COLUMN IF NOT EXISTS fees_usd DOUBLE PRECISION NOT NULL DEFAULT 0;