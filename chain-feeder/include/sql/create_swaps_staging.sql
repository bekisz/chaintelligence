-- ============================================================================
-- Switchover: replace the permanent `swaps` table with a short-lived staging
-- raw store plus an ingestion watermark table.
--
-- `swaps_staging` holds raw swap legs only long enough to be classified and
-- aggregated (route_daily_stats, route_daily_stats_bucket,
-- liquidity_pool_daily_stats). Old partitions are dropped after aggregation.
-- `ingestion_state` replaces `MAX(swaps.ts)` ingestion cursors so the ETL no
-- longer depends on the permanent table.
--
-- Apply via:
--   psql "$DATA_WAREHOUSE_DB" -f create_swaps_staging.sql
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Ingestion watermark (network, protocol) -> last ingested ts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_state (
    network    VARCHAR(20) NOT NULL,
    protocol   VARCHAR(50) NOT NULL,
    last_ts    TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (network, protocol)
);

-- ---------------------------------------------------------------------------
-- 2. Staging raw swap legs (partitioned by month, mirror of swaps columns)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS swaps_staging (
    tx_hash    VARCHAR(80) NOT NULL,
    log_index  INT NOT NULL,
    ts         TIMESTAMPTZ NOT NULL,
    network    VARCHAR(20) NOT NULL DEFAULT 'Ethereum',
    protocol   VARCHAR(50) NOT NULL DEFAULT 'Uniswap V3',
    pool_id    INTEGER REFERENCES liquidity_pool(id),
    amount0    DOUBLE PRECISION,
    amount1    DOUBLE PRECISION,
    amount_usd DOUBLE PRECISION,
    route_id   BIGINT REFERENCES route(route_id),
    PRIMARY KEY (ts, tx_hash, log_index)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS swaps_staging_2026_03 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_04 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_05 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_06 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_07 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_08 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_09 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_10 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_11 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS swaps_staging_2026_12 PARTITION OF swaps_staging
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS swaps_staging_default PARTITION OF swaps_staging DEFAULT;

CREATE INDEX IF NOT EXISTS idx_swaps_staging_tx ON swaps_staging (tx_hash);
CREATE INDEX IF NOT EXISTS idx_swaps_staging_pool_ts ON swaps_staging (pool_id, ts)
    INCLUDE (amount_usd, amount0, amount1);
CREATE INDEX IF NOT EXISTS idx_swaps_staging_route ON swaps_staging (route_id)
    WHERE route_id IS NOT NULL;

-- Backfill a default for :statement
ANALYZE swaps_staging;