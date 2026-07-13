-- ============================================================================
-- Migrate `liquidity_pool_position_snapshot` to Partitioned Table
-- Run via: docker exec -it chaintelligence-postgres-1 psql -U airflow -d chaintelligence -f /docker-entrypoint-initdb.d/migrate_snapshots_partitioning.sql
-- ============================================================================

BEGIN;

-- 1. Drop the dependent view
DROP VIEW IF EXISTS v_lp_snapshots_summary;

-- 2. Rename the old table
ALTER TABLE liquidity_pool_position_snapshot RENAME TO liquidity_pool_position_snapshot_old;
-- Also rename the old indexes so they don't clash
ALTER INDEX IF EXISTS idx_snapshot_pos_time RENAME TO idx_snapshot_pos_time_old;
ALTER INDEX IF EXISTS idx_snapshot_time RENAME TO idx_snapshot_time_old;

-- 3. Create the new partitioned table
CREATE TABLE liquidity_pool_position_snapshot (
    id SERIAL,
    position_id INT REFERENCES liquidity_pool_position(id),
    timestamp TIMESTAMP NOT NULL,
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
    
    -- Partition keys must be part of the primary key
    PRIMARY KEY (timestamp, id)
) PARTITION BY RANGE (timestamp);

-- 4. Create partitions (from Jan 2024 to Jan 2027)
CREATE TABLE liquidity_pool_position_snapshot_2024_01 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_02 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_03 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-03-01') TO ('2024-04-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_04 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-04-01') TO ('2024-05-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_05 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-05-01') TO ('2024-06-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_06 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-06-01') TO ('2024-07-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_07 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-07-01') TO ('2024-08-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_08 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-08-01') TO ('2024-09-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_09 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-09-01') TO ('2024-10-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_10 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-10-01') TO ('2024-11-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_11 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-11-01') TO ('2024-12-01');
CREATE TABLE liquidity_pool_position_snapshot_2024_12 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2024-12-01') TO ('2025-01-01');

CREATE TABLE liquidity_pool_position_snapshot_2025_01 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_02 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_03 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_04 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_05 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_06 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_07 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_08 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_09 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_10 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_11 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE liquidity_pool_position_snapshot_2025_12 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');

CREATE TABLE liquidity_pool_position_snapshot_2026_01 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_02 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_03 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_04 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_05 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_06 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_07 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_08 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_09 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_10 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_11 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE liquidity_pool_position_snapshot_2026_12 PARTITION OF liquidity_pool_position_snapshot FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE TABLE liquidity_pool_position_snapshot_default PARTITION OF liquidity_pool_position_snapshot DEFAULT;

-- 5. Copy data (specifying overriding system value so we keep the exact IDs)
INSERT INTO liquidity_pool_position_snapshot (
    id, position_id, timestamp, balance_usd, 
    coin0_amount, coin1_amount, 
    coin0_claimable_amount, coin1_claimable_amount,
    coin0_claimed_amount, coin1_claimed_amount,
    current_tick, current_price, in_range
)
SELECT 
    id, position_id, timestamp, balance_usd, 
    coin0_amount, coin1_amount, 
    coin0_claimable_amount, coin1_claimable_amount,
    coin0_claimed_amount, coin1_claimed_amount,
    current_tick, current_price, in_range
FROM liquidity_pool_position_snapshot_old;

-- 6. Synchronize sequence so new inserts don't fail with duplicate key
SELECT setval(
    pg_get_serial_sequence('liquidity_pool_position_snapshot', 'id'), 
    COALESCE((SELECT MAX(id) FROM liquidity_pool_position_snapshot), 0) + 1, 
    false
);

-- 7. Create indexes on the new partitioned table
CREATE INDEX idx_snapshot_pos_time ON liquidity_pool_position_snapshot(position_id, timestamp DESC);
CREATE INDEX idx_snapshot_time ON liquidity_pool_position_snapshot(timestamp DESC);

-- 8. Re-create the view
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
    0 as asset0_usd, 
    c1.symbol as asset1_symbol,
    s.coin1_amount as asset1_amount,
    0 as asset1_usd,
    
    -- Reconstruct Rewards (Use Claimable Columns)
    c0.symbol as reward0_symbol,
    s.coin0_claimable_amount as reward0_amount,
    0 as reward0_usd,
    c1.symbol as reward1_symbol,
    s.coin1_claimable_amount as reward1_amount,
    0 as reward1_usd,

    -- Reconstruct JSONs for legacy app
    jsonb_build_array(
        jsonb_build_object('symbol', c0.symbol, 'balance', s.coin0_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', c1.symbol, 'balance', s.coin1_amount, 'balanceUSD', 0)
    ) as assets,
    
    jsonb_build_array(
        jsonb_build_object('symbol', c0.symbol, 'balance', s.coin0_claimable_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', c1.symbol, 'balance', s.coin1_claimable_amount, 'balanceUSD', 0)
    ) as unclaimed,
     
    -- Images (From Coins)
    jsonb_build_array(
        c0.image_url,
        c1.image_url
    ) as images,
    
    -- Total unclaimed (Approximation, no USD value stored yet)
    0 as total_unclaimed_usd,
    
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

-- Note: In a production setting after confirming the migration is successful, 
-- you would eventually run: DROP TABLE liquidity_pool_position_snapshot_old;
