-- ============================================================================
-- Phase 2: Create unified `swaps` table with coin_id FKs
--
-- This replaces uniswap_v3_swaps, uniswap_v4_swaps, uniswap_v2_swaps.
--
-- Run in sections via psql:
--   psql "$DATA_WAREHOUSE_DB" -f create_swaps_table.sql
--
-- Pre-requisites:
--   - Phase 1 applied (NUMERIC → DOUBLE PRECISION done)
--   - coin.coin_id column exists (run the ALTER in this script if not)
--   - Missing tracked tokens added to coin table (inserts in this script)
-- ============================================================================

-- ============================================================================
-- PART 1: Ensure coin_id and tracked tokens
-- ============================================================================

-- 1a. Add coin_id SERIAL to coin table (idempotent)
ALTER TABLE coin ADD COLUMN IF NOT EXISTS coin_id SERIAL;

-- 1b. Add missing tracked tokens that the DAGs reference
INSERT INTO coin (symbol, hardness, ethereum_address, name, decimals) VALUES
  ('GMX',   820, '0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a', 'GMX', 18),
  ('USDC.E', 1000, '0xff970a61a04b1ca14834a43f5de4533ebddb5cc8', 'Bridged USDC', 6),
  ('USDbC', 1000, '0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA', 'USD Base Coin', 6)
ON CONFLICT (symbol) DO NOTHING;

-- ============================================================================
-- PART 2: Create the swaps table (partitioned by month)
-- ============================================================================

-- Drop the old function used by the swap tables' INSERT ON CONFLICT
-- The new table uses (tx_hash, log_index) PK instead of id VARCHAR(255)

CREATE TABLE IF NOT EXISTS swaps (
    tx_hash         VARCHAR(80) NOT NULL,
    log_index       INT NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    network         VARCHAR(20) NOT NULL DEFAULT 'Ethereum',
    protocol        VARCHAR(50) NOT NULL DEFAULT 'Uniswap V3',
    t0_coin_id      SMALLINT NOT NULL REFERENCES coin(coin_id),
    t1_coin_id      SMALLINT NOT NULL REFERENCES coin(coin_id),
    amount0         DOUBLE PRECISION,
    amount1         DOUBLE PRECISION,
    amount_usd      DOUBLE PRECISION,
    fee_bps         DOUBLE PRECISION,   -- 5 = 0.05%, 30 = 0.3%, NULL = Dynamic
    fee_display     VARCHAR(20),        -- original display format, e.g. "0.05%"
    PRIMARY KEY (ts, tx_hash, log_index)   -- PK must include partition key
) PARTITION BY RANGE (ts);

-- ============================================================================
-- PART 3: Create monthly partitions
-- ============================================================================
-- Current range: mid-2025 through mid-2026
-- Adjust as needed for your data range.

CREATE TABLE IF NOT EXISTS swaps_2025_06 PARTITION OF swaps
    FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
CREATE TABLE IF NOT EXISTS swaps_2025_07 PARTITION OF swaps
    FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
CREATE TABLE IF NOT EXISTS swaps_2025_08 PARTITION OF swaps
    FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
CREATE TABLE IF NOT EXISTS swaps_2025_09 PARTITION OF swaps
    FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
CREATE TABLE IF NOT EXISTS swaps_2025_10 PARTITION OF swaps
    FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
CREATE TABLE IF NOT EXISTS swaps_2025_11 PARTITION OF swaps
    FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
CREATE TABLE IF NOT EXISTS swaps_2025_12 PARTITION OF swaps
    FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
CREATE TABLE IF NOT EXISTS swaps_2026_01 PARTITION OF swaps
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE IF NOT EXISTS swaps_2026_02 PARTITION OF swaps
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE IF NOT EXISTS swaps_2026_03 PARTITION OF swaps
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS swaps_2026_04 PARTITION OF swaps
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS swaps_2026_05 PARTITION OF swaps
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS swaps_2026_06 PARTITION OF swaps
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS swaps_2026_07 PARTITION OF swaps
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS swaps_2026_08 PARTITION OF swaps
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS swaps_2026_09 PARTITION OF swaps
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS swaps_2026_10 PARTITION OF swaps
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS swaps_2026_11 PARTITION OF swaps
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS swaps_2026_12 PARTITION OF swaps
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- Default partition for any data outside the above range
CREATE TABLE IF NOT EXISTS swaps_default PARTITION OF swaps DEFAULT;

-- ============================================================================
-- PART 4: Insert into init_db.sql (reference only — not executed here)
-- ============================================================================
-- The above DDL should be added to chain-feeder/include/sql/init_db.sql
-- so new deployments auto-create the swaps table.

-- ============================================================================
-- End of DDL — run migrate_swaps_data.sql next to populate data
-- Then run create_swaps_indexes.sql to create indexes per partition
-- ============================================================================