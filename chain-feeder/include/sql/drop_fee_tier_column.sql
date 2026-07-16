-- ============================================================================
-- SQL Migration: Drop fee_tier column and migrate to fee_bps
-- ============================================================================

BEGIN;

-- 1. Merge overlapping history records for duplicate pools
CREATE TEMP TABLE temp_history_merge AS
SELECT 
    CASE 
        WHEN pool_id = 4701 THEN 128988
        WHEN pool_id = 694 THEN 128983
        WHEN pool_id = 11739 THEN 129001
        WHEN pool_id = 299 THEN 130865
        ELSE pool_id
    END AS pool_id,
    date,
    SUM(tx_count) AS tx_count,
    SUM(volume_usd) AS volume_usd,
    MAX(tvl_usd) AS tvl_usd
FROM liquidity_pool_history
WHERE pool_id IN (4701, 128988, 694, 128983, 11739, 129001, 299, 130865)
GROUP BY date, CASE 
    WHEN pool_id = 4701 THEN 128988
    WHEN pool_id = 694 THEN 128983
    WHEN pool_id = 11739 THEN 129001
    WHEN pool_id = 299 THEN 130865
    ELSE pool_id
END;

DELETE FROM liquidity_pool_history WHERE pool_id IN (4701, 128988, 694, 128983, 11739, 129001, 299, 130865);

INSERT INTO liquidity_pool_history (pool_id, date, tx_count, volume_usd, tvl_usd)
SELECT pool_id, date, tx_count, volume_usd, tvl_usd FROM temp_history_merge;

DELETE FROM liquidity_pool WHERE id IN (4701, 694, 11739, 299);

-- 2. Drop existing unique constraint that depends on fee_tier
ALTER TABLE liquidity_pool DROP CONSTRAINT IF EXISTS liquidity_pool_chain_protocol_name_fee_key;

-- 3. Add new unique constraint on fee_bps
ALTER TABLE liquidity_pool ADD CONSTRAINT liquidity_pool_chain_protocol_name_fee_bps_key UNIQUE (chain_id, protocol_id, pool_name, fee_bps);

-- 4. Drop legacy fee_tier column
ALTER TABLE liquidity_pool DROP COLUMN IF EXISTS fee_tier;

-- 4. Recreate v_lp_snapshots_summary compatibility view
DROP VIEW IF EXISTS v_lp_snapshots_summary CASCADE;

CREATE OR REPLACE VIEW v_lp_snapshots_summary AS
SELECT 
    s.id,
    s.timestamp,
    pos.wallet_address AS address,
    pos.position_key,
    pr.name AS protocol,
    ch.name AS network,
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
    CASE WHEN pool.fee_bps IS NULL THEN 'Dynamic' ELSE (pool.fee_bps / 100.0)::text || '%' END AS fee_tier,
    s.coin0_claimed_amount,
    s.coin1_claimed_amount
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id
JOIN chain ch ON pool.chain_id = ch.id
JOIN protocol pr ON pool.protocol_id = pr.id
JOIN coin c0 ON pool.coin0_id = c0.coin_id
JOIN coin c1 ON pool.coin1_id = c1.coin_id;

COMMIT;
