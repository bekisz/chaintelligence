BEGIN;

-- 1. Add fee_tier to liquidity_pool_position
ALTER TABLE liquidity_pool_position ADD COLUMN IF NOT EXISTS fee_tier VARCHAR(10);

-- Backfill from pool (best effort)
UPDATE liquidity_pool_position pos
SET fee_tier = pool.fee_tier
FROM liquidity_pool pool
WHERE pos.pool_id = pool.id AND pos.fee_tier IS NULL;

-- 2. Deduplicate Pools
CREATE TEMP TABLE pool_survivors AS
SELECT DISTINCT ON (network, protocol, pool_name)
    id as survivor_id,
    network, protocol, pool_name
FROM liquidity_pool
ORDER BY network, protocol, pool_name, id ASC;

-- Update FKs
UPDATE liquidity_pool_position pos
SET pool_id = s.survivor_id
FROM liquidity_pool p
JOIN pool_survivors s ON p.network = s.network AND p.protocol = s.protocol AND p.pool_name = s.pool_name
WHERE pos.pool_id = p.id AND pos.pool_id != s.survivor_id;

-- Delete non-survivors
DELETE FROM liquidity_pool
WHERE id NOT IN (SELECT survivor_id FROM pool_survivors);

-- 3. Update Unique Constraint (Strictly Unique by Name now)
ALTER TABLE liquidity_pool DROP CONSTRAINT IF EXISTS liquidity_pool_network_protocol_pool_name_fee_tier_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_pool_name ON liquidity_pool (network, protocol, pool_name);

-- 4. Update View (With corrected calculation and fee_tier source)
DROP VIEW IF EXISTS v_lp_snapshots_summary;

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
    
    pool.coin0_symbol as asset0_symbol,
    s.coin0_amount as asset0_amount,
    0 as asset0_usd, 
    pool.coin1_symbol as asset1_symbol,
    s.coin1_amount as asset1_amount,
    0 as asset1_usd,
    
    pool.coin0_symbol as reward0_symbol,
    s.coin0_claimable_amount as reward0_amount,
    0 as reward0_usd,
    pool.coin1_symbol as reward1_symbol,
    s.coin1_claimable_amount as reward1_amount,
    0 as reward1_usd,

    jsonb_build_array(
        jsonb_build_object('symbol', pool.coin0_symbol, 'balance', s.coin0_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', pool.coin1_symbol, 'balance', s.coin1_amount, 'balanceUSD', 0)
    ) as assets,
    
    jsonb_build_array(
        jsonb_build_object('symbol', pool.coin0_symbol, 'balance', s.coin0_claimable_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', pool.coin1_symbol, 'balance', s.coin1_claimable_amount, 'balanceUSD', 0)
    ) as unclaimed,
     
    jsonb_build_array(
        (SELECT image_url FROM coin WHERE symbol = pool.coin0_symbol),
        (SELECT image_url FROM coin WHERE symbol = pool.coin1_symbol)
    ) as images,
    
    -- Total unclaimed (Corrected Logic for Stablecoins)
    CASE 
        WHEN s.balance_usd > 0 THEN
            CASE 
                -- Inverted Case: T0 is Stable, T1 is not. P = Price(T1)/Price(T0)
                WHEN UPPER(pool.coin0_symbol) IN ('USDC', 'USDT', 'DAI', 'USDBC', 'USDB', 'EUROC', 'EURC') 
                     AND UPPER(pool.coin1_symbol) NOT IN ('USDC', 'USDT', 'DAI', 'USDBC', 'USDB', 'EUROC', 'EURC') THEN
                    (s.balance_usd * (s.coin0_claimable_amount + s.coin1_claimable_amount * COALESCE(s.current_price, pos.current_price, 0))) / 
                    NULLIF(s.coin0_amount + s.coin1_amount * COALESCE(s.current_price, pos.current_price, 0), 0)
                
                -- Standard Case
                ELSE
                    (s.balance_usd * (s.coin0_claimable_amount * COALESCE(s.current_price, pos.current_price, 0) + s.coin1_claimable_amount)) / 
                    NULLIF(s.coin0_amount * COALESCE(s.current_price, pos.current_price, 0) + s.coin1_amount, 0)
            END
        ELSE 0 
    END as total_unclaimed_usd,
    
    pos.token_id,
    pos.tick_lower,
    pos.tick_upper,
    COALESCE(s.current_tick, pos.current_tick) as current_tick,
    pos.price_lower,
    pos.price_upper,
    COALESCE(s.current_price, pos.current_price) as current_price,
    
    CASE 
        WHEN COALESCE(s.current_tick, pos.current_tick) IS NOT NULL AND pos.tick_lower IS NOT NULL 
        THEN (COALESCE(s.current_tick, pos.current_tick) >= pos.tick_lower AND COALESCE(s.current_tick, pos.current_tick) <= pos.tick_upper)
        ELSE NULL 
    END as in_range,
    
    -- Prefer Position Fee Tier
    COALESCE(pos.fee_tier, pool.fee_tier) as fee_tier
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id;

COMMIT;
