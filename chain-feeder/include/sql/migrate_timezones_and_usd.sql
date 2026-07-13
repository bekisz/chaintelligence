-- ============================================================================
-- Migrate Timezones and Add USD Snapshot Tracking
-- Run via: docker exec -it chaintelligence-postgres-1 psql -U airflow -d chaintelligence -f /docker-entrypoint-initdb.d/migrate_timezones_and_usd.sql
-- ============================================================================

BEGIN;

-- 1. Drop dependent view
DROP VIEW IF EXISTS v_lp_snapshots_summary;

-- 2. Standardize timezones for non-partitioned tables
ALTER TABLE liquidity_pool_position 
  ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';

ALTER TABLE liquidity_pool 
  ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';

-- 3. We cannot alter the type of a partition key in Postgres. 
-- So we must create a new table, copy data, and rename.
CREATE TABLE liquidity_pool_position_snapshot_new (
    id SERIAL,
    position_id INT REFERENCES liquidity_pool_position(id),
    timestamp TIMESTAMPTZ NOT NULL,
    balance_usd NUMERIC,
    
    -- Flattened Assets
    coin0_amount NUMERIC,
    coin1_amount NUMERIC,

    -- Flattened Claimable
    coin0_claimable_amount NUMERIC,
    coin1_claimable_amount NUMERIC,
    
    -- Flattened Claimed
    coin0_claimed_amount NUMERIC,
    coin1_claimed_amount NUMERIC,
    
    current_tick INTEGER,
    current_price NUMERIC,
    in_range BOOLEAN,
    
    -- NEW USD COLUMNS
    coin0_usd NUMERIC,
    coin1_usd NUMERIC,
    coin0_claimable_usd NUMERIC,
    coin1_claimable_usd NUMERIC,

    PRIMARY KEY (timestamp, id)
) PARTITION BY RANGE (timestamp);

-- Create partitions for the new table
CREATE TABLE liquidity_pool_position_snapshot_new_2024_01 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_02 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_03 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-03-01') TO ('2024-04-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_04 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-04-01') TO ('2024-05-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_05 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-05-01') TO ('2024-06-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_06 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-06-01') TO ('2024-07-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_07 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-07-01') TO ('2024-08-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_08 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-08-01') TO ('2024-09-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_09 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-09-01') TO ('2024-10-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_10 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-10-01') TO ('2024-11-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_11 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-11-01') TO ('2024-12-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2024_12 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2024-12-01') TO ('2025-01-01');

CREATE TABLE liquidity_pool_position_snapshot_new_2025_01 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_02 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_03 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_04 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_05 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_06 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_07 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_08 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_09 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_10 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_11 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2025_12 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');

CREATE TABLE liquidity_pool_position_snapshot_new_2026_01 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_02 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_03 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_04 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_05 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_06 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_07 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_08 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_09 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_10 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_11 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE liquidity_pool_position_snapshot_new_2026_12 PARTITION OF liquidity_pool_position_snapshot_new FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE TABLE liquidity_pool_position_snapshot_new_default PARTITION OF liquidity_pool_position_snapshot_new DEFAULT;

-- Insert data from old table, timezone will be converted implicitly
INSERT INTO liquidity_pool_position_snapshot_new (
    id, position_id, timestamp, balance_usd, 
    coin0_amount, coin1_amount, 
    coin0_claimable_amount, coin1_claimable_amount,
    coin0_claimed_amount, coin1_claimed_amount,
    current_tick, current_price, in_range
)
SELECT 
    id, position_id, timestamp AT TIME ZONE 'UTC', balance_usd, 
    coin0_amount, coin1_amount, 
    coin0_claimable_amount, coin1_claimable_amount,
    coin0_claimed_amount, coin1_claimed_amount,
    current_tick, current_price, in_range
FROM liquidity_pool_position_snapshot;

-- Drop old table
DROP TABLE liquidity_pool_position_snapshot CASCADE;

-- Rename new table
ALTER TABLE liquidity_pool_position_snapshot_new RENAME TO liquidity_pool_position_snapshot;

-- Rename partitions
ALTER TABLE liquidity_pool_position_snapshot_new_2024_01 RENAME TO liquidity_pool_position_snapshot_2024_01;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_02 RENAME TO liquidity_pool_position_snapshot_2024_02;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_03 RENAME TO liquidity_pool_position_snapshot_2024_03;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_04 RENAME TO liquidity_pool_position_snapshot_2024_04;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_05 RENAME TO liquidity_pool_position_snapshot_2024_05;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_06 RENAME TO liquidity_pool_position_snapshot_2024_06;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_07 RENAME TO liquidity_pool_position_snapshot_2024_07;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_08 RENAME TO liquidity_pool_position_snapshot_2024_08;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_09 RENAME TO liquidity_pool_position_snapshot_2024_09;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_10 RENAME TO liquidity_pool_position_snapshot_2024_10;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_11 RENAME TO liquidity_pool_position_snapshot_2024_11;
ALTER TABLE liquidity_pool_position_snapshot_new_2024_12 RENAME TO liquidity_pool_position_snapshot_2024_12;

ALTER TABLE liquidity_pool_position_snapshot_new_2025_01 RENAME TO liquidity_pool_position_snapshot_2025_01;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_02 RENAME TO liquidity_pool_position_snapshot_2025_02;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_03 RENAME TO liquidity_pool_position_snapshot_2025_03;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_04 RENAME TO liquidity_pool_position_snapshot_2025_04;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_05 RENAME TO liquidity_pool_position_snapshot_2025_05;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_06 RENAME TO liquidity_pool_position_snapshot_2025_06;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_07 RENAME TO liquidity_pool_position_snapshot_2025_07;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_08 RENAME TO liquidity_pool_position_snapshot_2025_08;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_09 RENAME TO liquidity_pool_position_snapshot_2025_09;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_10 RENAME TO liquidity_pool_position_snapshot_2025_10;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_11 RENAME TO liquidity_pool_position_snapshot_2025_11;
ALTER TABLE liquidity_pool_position_snapshot_new_2025_12 RENAME TO liquidity_pool_position_snapshot_2025_12;

ALTER TABLE liquidity_pool_position_snapshot_new_2026_01 RENAME TO liquidity_pool_position_snapshot_2026_01;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_02 RENAME TO liquidity_pool_position_snapshot_2026_02;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_03 RENAME TO liquidity_pool_position_snapshot_2026_03;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_04 RENAME TO liquidity_pool_position_snapshot_2026_04;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_05 RENAME TO liquidity_pool_position_snapshot_2026_05;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_06 RENAME TO liquidity_pool_position_snapshot_2026_06;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_07 RENAME TO liquidity_pool_position_snapshot_2026_07;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_08 RENAME TO liquidity_pool_position_snapshot_2026_08;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_09 RENAME TO liquidity_pool_position_snapshot_2026_09;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_10 RENAME TO liquidity_pool_position_snapshot_2026_10;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_11 RENAME TO liquidity_pool_position_snapshot_2026_11;
ALTER TABLE liquidity_pool_position_snapshot_new_2026_12 RENAME TO liquidity_pool_position_snapshot_2026_12;

ALTER TABLE liquidity_pool_position_snapshot_new_default RENAME TO liquidity_pool_position_snapshot_default;

-- Create indexes on the new partitioned table
CREATE INDEX idx_snapshot_pos_time ON liquidity_pool_position_snapshot(position_id, timestamp DESC);
CREATE INDEX idx_snapshot_time ON liquidity_pool_position_snapshot(timestamp DESC);

-- 4. Recreate the summary view using the new USD columns
CREATE OR REPLACE VIEW v_lp_snapshots_summary AS
SELECT 
    s.id,
    s.timestamp,
    pos.wallet_address AS address,
    pos.position_key,
    pool.protocol,
    pool.network,
    CASE 
        WHEN pos.token_id IS NOT NULL THEN pool.pool_name || ' (Token ID: ' || pos.token_id || ')' 
        ELSE pool.pool_name 
    END AS position_label,
    s.balance_usd,
    
    -- Reconstruct Assets
    c0.symbol as asset0_symbol,
    s.coin0_amount as asset0_amount,
    COALESCE(s.coin0_usd, 0) as asset0_usd, 
    c1.symbol as asset1_symbol,
    s.coin1_amount as asset1_amount,
    COALESCE(s.coin1_usd, 0) as asset1_usd,
    
    -- Reconstruct Rewards (Use Claimable Columns)
    c0.symbol as reward0_symbol,
    s.coin0_claimable_amount as reward0_amount,
    COALESCE(s.coin0_claimable_usd, 0) as reward0_usd,
    c1.symbol as reward1_symbol,
    s.coin1_claimable_amount as reward1_amount,
    COALESCE(s.coin1_claimable_usd, 0) as reward1_usd,

    -- Reconstruct JSONs for legacy app
    jsonb_build_array(
        jsonb_build_object('symbol', c0.symbol, 'balance', s.coin0_amount, 'balanceUSD', COALESCE(s.coin0_usd, 0)),
        jsonb_build_object('symbol', c1.symbol, 'balance', s.coin1_amount, 'balanceUSD', COALESCE(s.coin1_usd, 0))
    ) as assets,
    
    jsonb_build_array(
        jsonb_build_object('symbol', c0.symbol, 'balance', s.coin0_claimable_amount, 'balanceUSD', COALESCE(s.coin0_claimable_usd, 0)),
        jsonb_build_object('symbol', c1.symbol, 'balance', s.coin1_claimable_amount, 'balanceUSD', COALESCE(s.coin1_claimable_usd, 0))
    ) as unclaimed,
     
    -- Images (From Coins)
    jsonb_build_array(
        c0.image_url,
        c1.image_url
    ) as images,
    
    -- Total unclaimed (Sum of new USD columns)
    COALESCE(s.coin0_claimable_usd, 0) + COALESCE(s.coin1_claimable_usd, 0) as total_unclaimed_usd,
    
    -- Range data
    pos.token_id,
    pos.tick_lower,
    pos.tick_upper,
    s.current_tick,
    pos.price_lower,
    pos.price_upper,
    s.current_price,
    s.in_range,
    pool.fee_tier,
    s.coin0_claimed_amount,
    s.coin1_claimed_amount
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id
JOIN coin c0 ON pool.coin0_id = c0.coin_id
JOIN coin c1 ON pool.coin1_id = c1.coin_id;

COMMIT;
