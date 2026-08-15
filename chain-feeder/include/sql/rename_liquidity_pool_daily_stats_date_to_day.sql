-- Align liquidity_pool_daily_stats column naming with route_daily_stats:
-- the daily-granularity column is `day` on both tables.
-- Apply only AFTER: rename_liquidity_pool_history_to_daily_stats.sql
--                AND make_liquidity_pool_daily_stats_composite_pk.sql
--   psql "$DATA_WAREHOUSE_DB" -f rename_liquidity_pool_daily_stats_date_to_day.sql
ALTER TABLE IF EXISTS liquidity_pool_daily_stats RENAME COLUMN date TO day;
ALTER INDEX IF EXISTS idx_lp_daily_stats_date RENAME TO idx_lp_daily_stats_day;