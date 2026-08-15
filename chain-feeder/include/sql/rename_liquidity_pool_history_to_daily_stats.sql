-- Rename liquidity_pool_history to liquidity_pool_daily_stats.
-- Apply to an existing warehouse:
--   psql "$DATA_WAREHOUSE_DB" -f rename_liquidity_pool_history_to_daily_stats.sql
ALTER TABLE IF EXISTS liquidity_pool_history RENAME TO liquidity_pool_daily_stats;
ALTER INDEX IF EXISTS idx_lp_history_date RENAME TO idx_lp_daily_stats_date;
ALTER INDEX IF EXISTS idx_lp_history_pool RENAME TO idx_lp_daily_stats_pool;