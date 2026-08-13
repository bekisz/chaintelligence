-- Configurable, compact swap-size distributions for selected routes.

CREATE TABLE IF NOT EXISTS route_distribution_config (
    route_id        BIGINT PRIMARY KEY REFERENCES route(route_id) ON DELETE CASCADE,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    bucket_count    SMALLINT NOT NULL DEFAULT 80 CHECK (bucket_count BETWEEN 8 AND 256),
    min_amount_usd  DOUBLE PRECISION NOT NULL DEFAULT 10.0 CHECK (min_amount_usd > 0),
    max_amount_usd  DOUBLE PRECISION NOT NULL DEFAULT 100000000.0
        CHECK (max_amount_usd > min_amount_usd),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS route_distribution_bucket (
    route_id       BIGINT NOT NULL REFERENCES route(route_id) ON DELETE CASCADE,
    day            DATE NOT NULL,
    bucket_index   SMALLINT NOT NULL,
    sample_count   BIGINT NOT NULL DEFAULT 0,
    volume_usd     DOUBLE PRECISION NOT NULL DEFAULT 0,
    log_sum        DOUBLE PRECISION NOT NULL DEFAULT 0,
    log_sum2       DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (route_id, day, bucket_index),
    CHECK (bucket_index >= 1)
);

CREATE INDEX IF NOT EXISTS idx_route_distribution_bucket_day
    ON route_distribution_bucket (day, route_id);
