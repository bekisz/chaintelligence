DROP VIEW IF EXISTS v_lp_snapshots_summary;

CREATE VIEW v_lp_snapshots_summary AS
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
    
    -- Total unclaimed
    CASE 
        WHEN s.balance_usd > 0 THEN
            CASE 
                WHEN pool.reverted IS TRUE THEN
                    (s.balance_usd * (s.coin0_claimable_amount + s.coin1_claimable_amount * COALESCE(s.current_price, pos.current_price, 0))) / 
                    NULLIF(s.coin0_amount + s.coin1_amount * COALESCE(s.current_price, pos.current_price, 0), 0)
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
    s.in_range,
    pool.reverted,
    pool.pool_address,
    COALESCE(pos.fee_tier, pool.fee_tier) as fee_tier
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id;
