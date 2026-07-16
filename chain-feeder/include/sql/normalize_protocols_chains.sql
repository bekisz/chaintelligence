-- ============================================================================
-- SQL Migration: Normalize network and protocol to SMALLINT lookup tables
-- ============================================================================

BEGIN;

-- Drop legacy compatibility views first (they depend on old columns)
DROP VIEW IF EXISTS uniswap_v3_swaps CASCADE;
DROP VIEW IF EXISTS uniswap_v2_swaps CASCADE;
DROP VIEW IF EXISTS uniswap_v4_swaps CASCADE;

-- 1. Create Lookup Tables
CREATE TABLE IF NOT EXISTS chain (
    id SMALLINT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS protocol (
    id SMALLINT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE
);

-- 2. Populate Lookup Tables
INSERT INTO chain (id, name) VALUES
    (1, 'Ethereum'),
    (2, 'Arbitrum'),
    (3, 'Base'),
    (4, 'BNB'),
    (5, 'Solana')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;

INSERT INTO protocol (id, name) VALUES
    (1, 'Uniswap V2'),
    (2, 'Uniswap V3'),
    (3, 'Uniswap V4'),
    (4, 'PancakeSwap V3'),
    (5, 'PancakeSwap V4'),
    (6, 'Aerodrome')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;


-- 3. Migrate `liquidity_pool` Table
-- Drop unique constraint that uses network/protocol strings
ALTER TABLE liquidity_pool DROP CONSTRAINT IF EXISTS liquidity_pool_network_protocol_pool_name_fee_tier_key;

-- Add chain_id and protocol_id columns
ALTER TABLE liquidity_pool ADD COLUMN IF NOT EXISTS chain_id SMALLINT REFERENCES chain(id);
ALTER TABLE liquidity_pool ADD COLUMN IF NOT EXISTS protocol_id SMALLINT REFERENCES protocol(id);

-- Populate new columns
UPDATE liquidity_pool lp
SET chain_id = c.id
FROM chain c
WHERE LOWER(lp.network) = LOWER(c.name);

UPDATE liquidity_pool lp
SET protocol_id = p.id
FROM protocol p
WHERE LOWER(lp.protocol) = LOWER(p.name);

-- Enforce NOT NULL constraints
ALTER TABLE liquidity_pool ALTER COLUMN chain_id SET NOT NULL;
ALTER TABLE liquidity_pool ALTER COLUMN protocol_id SET NOT NULL;

-- Drop old string columns
ALTER TABLE liquidity_pool DROP COLUMN IF EXISTS network;
ALTER TABLE liquidity_pool DROP COLUMN IF EXISTS protocol;

-- Recreate unique constraint using normalized ID columns
ALTER TABLE liquidity_pool ADD CONSTRAINT liquidity_pool_chain_protocol_name_fee_key UNIQUE (chain_id, protocol_id, pool_name, fee_tier);


-- 4. Migrate `coin_contract` Table
-- Drop primary key constraint
ALTER TABLE coin_contract DROP CONSTRAINT IF EXISTS coin_contract_pkey;

-- Add chain_id column
ALTER TABLE coin_contract ADD COLUMN IF NOT EXISTS chain_id SMALLINT REFERENCES chain(id);

-- Populate chain_id
UPDATE coin_contract cc
SET chain_id = c.id
FROM chain c
WHERE LOWER(cc.chain) = CASE WHEN LOWER(c.name) = 'bnb' THEN 'bsc' ELSE LOWER(c.name) END;

-- Enforce NOT NULL
ALTER TABLE coin_contract ALTER COLUMN chain_id SET NOT NULL;

-- Drop old chain column
ALTER TABLE coin_contract DROP COLUMN IF EXISTS chain;

-- Recreate primary key
ALTER TABLE coin_contract ADD PRIMARY KEY (coin_id, chain_id);

-- Recreate unique index on contract address
DROP INDEX IF EXISTS idx_coin_contract_addr;
CREATE UNIQUE INDEX idx_coin_contract_addr ON coin_contract (chain_id, LOWER(contract_address));


-- 5. Migrate `swaps` Table
-- Drop indexes that INCLUDE network and protocol columns
DROP INDEX IF EXISTS idx_swaps_coin1_0_ts;
DROP INDEX IF EXISTS idx_swaps_coin_pair_ts;

-- Add new columns to parent swaps table (propagates to partitions)
ALTER TABLE swaps ADD COLUMN IF NOT EXISTS chain_id SMALLINT REFERENCES chain(id);
ALTER TABLE swaps ADD COLUMN IF NOT EXISTS protocol_id SMALLINT REFERENCES protocol(id);

-- Populate columns
UPDATE swaps s
SET chain_id = c.id
FROM chain c
WHERE LOWER(s.network) = LOWER(c.name);

UPDATE swaps s
SET protocol_id = p.id
FROM protocol p
WHERE LOWER(s.protocol) = LOWER(p.name);

-- Enforce NOT NULL
ALTER TABLE swaps ALTER COLUMN chain_id SET NOT NULL;
ALTER TABLE swaps ALTER COLUMN protocol_id SET NOT NULL;

-- Drop old columns
ALTER TABLE swaps DROP COLUMN IF EXISTS network;
ALTER TABLE swaps DROP COLUMN IF EXISTS protocol;

-- Recreate indexes using new ID columns
CREATE INDEX idx_swaps_coin1_0_ts ON swaps (t1_coin_id, t0_coin_id, ts) INCLUDE (amount_usd, chain_id, protocol_id, fee_bps, fee_display);
CREATE INDEX idx_swaps_coin_pair_ts ON swaps (t0_coin_id, t1_coin_id, ts) INCLUDE (amount_usd, chain_id, protocol_id, fee_bps, fee_display);


-- 6. Recreate Compatibility Views
-- V3 View
CREATE OR REPLACE VIEW uniswap_v3_swaps AS
SELECT 
    (s.tx_hash || '#' || s.log_index) AS id,
    s.tx_hash AS transaction,
    s.ts AS timestamp,
    c0.symbol AS token0_symbol,
    cc0.contract_address AS token0_address,
    c1.symbol AS token1_symbol,
    cc1.contract_address AS token1_address,
    s.amount0 AS amount0,
    s.amount1 AS amount1,
    s.amount_usd AS "amountUSD",
    s.fee_display AS "feeTier",
    ch.name AS network,
    p.name AS protocol
FROM swaps s
JOIN coin c0 ON s.t0_coin_id = c0.coin_id
JOIN coin c1 ON s.t1_coin_id = c1.coin_id
JOIN chain ch ON s.chain_id = ch.id
JOIN protocol p ON s.protocol_id = p.id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = s.t0_coin_id AND cc0.chain_id = s.chain_id
LEFT JOIN coin_contract cc1 ON cc1.coin_id = s.t1_coin_id AND cc1.chain_id = s.chain_id
WHERE p.name IN ('Uniswap V3', 'PancakeSwap V3', 'Aerodrome');

-- V2 View
CREATE OR REPLACE VIEW uniswap_v2_swaps AS
SELECT 
    ('v2-' || s.tx_hash || '-' || s.log_index) AS id,
    s.tx_hash AS transaction,
    s.ts AS timestamp,
    c0.symbol AS token0_symbol,
    cc0.contract_address AS token0_address,
    c1.symbol AS token1_symbol,
    cc1.contract_address AS token1_address,
    s.amount0 AS amount0,
    s.amount1 AS amount1,
    s.amount_usd AS "amountUSD",
    s.fee_display AS "feeTier",
    ch.name AS network,
    p.name AS protocol
FROM swaps s
JOIN coin c0 ON s.t0_coin_id = c0.coin_id
JOIN coin c1 ON s.t1_coin_id = c1.coin_id
JOIN chain ch ON s.chain_id = ch.id
JOIN protocol p ON s.protocol_id = p.id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = s.t0_coin_id AND cc0.chain_id = s.chain_id
LEFT JOIN coin_contract cc1 ON cc1.coin_id = s.t1_coin_id AND cc1.chain_id = s.chain_id
WHERE p.name = 'Uniswap V2';

-- V4 View
CREATE OR REPLACE VIEW uniswap_v4_swaps AS
SELECT 
    (s.tx_hash || '-' || s.log_index) AS id,
    s.tx_hash AS transaction,
    s.ts AS timestamp,
    c0.symbol AS token0_symbol,
    cc0.contract_address AS token0_address,
    c1.symbol AS token1_symbol,
    cc1.contract_address AS token1_address,
    s.amount0 AS amount0,
    s.amount1 AS amount1,
    s.amount_usd AS "amountUSD",
    s.fee_display AS "feeTier",
    ch.name AS network,
    p.name AS protocol
FROM swaps s
JOIN coin c0 ON s.t0_coin_id = c0.coin_id
JOIN coin c1 ON s.t1_coin_id = c1.coin_id
JOIN chain ch ON s.chain_id = ch.id
JOIN protocol p ON s.protocol_id = p.id
LEFT JOIN coin_contract cc0 ON cc0.coin_id = s.t0_coin_id AND cc0.chain_id = s.chain_id
LEFT JOIN coin_contract cc1 ON cc1.coin_id = s.t1_coin_id AND cc1.chain_id = s.chain_id
WHERE p.name IN ('Uniswap V4', 'PancakeSwap V4');

COMMIT;
