-- Configurable, compact swap-size distributions for selected routes and pools.
-- Bucket parameters are global (config/swap-distribution.yaml), not per-route.

CREATE TABLE IF NOT EXISTS route_daily_stats_bucket (
    route_id       BIGINT NOT NULL REFERENCES route(route_id) ON DELETE CASCADE,
    day            DATE NOT NULL,
    bucket_index   SMALLINT NOT NULL,
    tx_count       INT NOT NULL DEFAULT 0,
    sample_count   BIGINT NOT NULL DEFAULT 0,
    volume_usd     DOUBLE PRECISION NOT NULL DEFAULT 0,
    fees_usd       DOUBLE PRECISION NOT NULL DEFAULT 0,
    log_sum        DOUBLE PRECISION NOT NULL DEFAULT 0,
    log_sum2       DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (route_id, day, bucket_index),
    CHECK (bucket_index >= 1)
);

CREATE INDEX IF NOT EXISTS idx_route_daily_stats_bucket_day
    ON route_daily_stats_bucket (day, route_id);

-- Compact swap-size distributions for every liquidity pool.
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