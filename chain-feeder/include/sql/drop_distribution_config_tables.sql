-- Drop the per-route/per-pool distribution config tables.
--
-- Bucket parameters are now global (config/swap-distribution.yaml); the
-- route_daily_stats_bucket and liquidity_pool_daily_stats_bucket rollups bucket
-- every route/pool unconditionally and no longer consult these tables.
--
-- Apply to an existing warehouse:
--   psql "$DATA_WAREHOUSE_DB" -f drop_distribution_config_tables.sql

DROP TABLE IF EXISTS route_distribution_config CASCADE;
DROP TABLE IF EXISTS liquidity_pool_distribution_config CASCADE;