-- Rename route_distribution_bucket -> route_daily_stats_bucket, add tx_count
-- and fees_usd to it, and add the pool-grain equivalent
-- (liquidity_pool_daily_stats_bucket). The liquidity_pool_distribution_config
-- table is gone: bucket parameters are global (config/swap-distribution.yaml).
--
-- Apply to an existing warehouse:
--   psql "$DATA_WAREHOUSE_DB" -f rename_route_distribution_bucket_to_daily_stats_bucket.sql

ALTER TABLE IF EXISTS route_distribution_bucket RENAME TO route_daily_stats_bucket;
ALTER INDEX IF EXISTS idx_route_distribution_bucket_day RENAME TO idx_route_daily_stats_bucket_day;

ALTER TABLE route_daily_stats_bucket ADD COLUMN IF NOT EXISTS tx_count INT NOT NULL DEFAULT 0;
ALTER TABLE route_daily_stats_bucket ADD COLUMN IF NOT EXISTS fees_usd DOUBLE PRECISION NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS liquidity_pool_daily_stats_bucket (
    pool_id        INTEGER NOT NULL REFERENCES liquidity_pool(id) ON DELETE CASCADE,
    day            DATE NOT NULL,
    bucket_index   SMALLINT NOT NULL,
    tx_count       INT NOT NULL DEFAULT 0,
    sample_count   BIGINT NOT NULL DEFAULT 0,
    volume_usd     DOUBLE PRECISION NOT NULL DEFAULT 0,
    fees_usd       DOUBLE PRECISION NOT NULL DEFAULT 0,
    log_sum        DOUBLE PRECISION NOT NULL DEFAULT 0,
    log_sum2       DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (pool_id, day, bucket_index),
    CHECK (bucket_index >= 1)
);

CREATE INDEX IF NOT EXISTS idx_lp_daily_stats_bucket_day
    ON liquidity_pool_daily_stats_bucket (day, pool_id);
