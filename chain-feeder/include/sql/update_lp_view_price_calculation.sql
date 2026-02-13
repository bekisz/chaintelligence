-- Update v_lp_snapshots_summary to calculate current_price from coin prices
-- This fixes the issue where 99.96% of snapshots don't have current_tick data

DROP VIEW IF EXISTS v_lp_snapshots_summary CASCADE;

CREATE VIEW v_lp_snapshots_summary AS
SELECT 
    s.id,
    s.timestamp,
    pos.wallet_address AS address,
    pos.position_key,
    pool.protocol,
    pool.network,
    CASE 
        WHEN pos.token_id IS NOT NULL 
        THEN (pool.pool_name || ' (Token ID: ' || pos.token_id || ')')::VARCHAR
        ELSE pool.pool_name
    END AS position_label,
    s.balance_usd,
    pool.coin0_symbol AS asset0_symbol,
    s.coin0_amount AS asset0_amount,
    0 AS asset0_usd,
    pool.coin1_symbol AS asset1_symbol,
    s.coin1_amount AS asset1_amount,
    0 AS asset1_usd,
    pool.coin0_symbol AS reward0_symbol,
    s.coin0_claimable_amount AS reward0_amount,
    0 AS reward0_usd,
    pool.coin1_symbol AS reward1_symbol,
    s.coin1_claimable_amount AS reward1_amount,
    0 AS reward1_usd,
    jsonb_build_array(
        jsonb_build_object('symbol', pool.coin0_symbol, 'balance', s.coin0_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', pool.coin1_symbol, 'balance', s.coin1_amount, 'balanceUSD', 0)
    ) AS assets,
    jsonb_build_array(
        jsonb_build_object('symbol', pool.coin0_symbol, 'balance', s.coin0_claimable_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', pool.coin1_symbol, 'balance', s.coin1_claimable_amount, 'balanceUSD', 0)
    ) AS unclaimed,
    jsonb_build_array(
        (SELECT image_url FROM coin WHERE symbol = pool.coin0_symbol),
        (SELECT image_url FROM coin WHERE symbol = pool.coin1_symbol)
    ) AS images,
    0::NUMERIC AS total_unclaimed_usd,
    pos.token_id,
    pos.tick_lower,
    pos.tick_upper,
    s.current_tick,
    pos.price_lower,
    pos.price_upper,
    -- Calculate current_price with three-tier fallback:
    -- 1. Use stored current_price if available
    -- 2. Calculate from current_tick with decimal adjustment (for positions with tick data)
    -- 3. Calculate from individual coin prices as ratio (fallback for 99.96% of positions)
    COALESCE(
        s.current_price,
        -- Tick-based calculation with decimal adjustment
        CASE 
            WHEN s.current_tick IS NOT NULL THEN
                (POWER(1.0001, s.current_tick) * 
                POWER(10, 
                    COALESCE((SELECT decimals FROM coin WHERE symbol = pool.coin1_symbol), 18) - 
                    COALESCE((SELECT decimals FROM coin WHERE symbol = pool.coin0_symbol), 18)
                ))::NUMERIC
            ELSE NULL
        END,
        -- Fallback: coin0_price / coin1_price (e.g., ETH/USDC)
        (
            (SELECT price FROM coin WHERE symbol = pool.coin0_symbol) / 
            NULLIF((SELECT price FROM coin WHERE symbol = pool.coin1_symbol), 0)
        )
    ) AS current_price,
    s.in_range,
    pool.fee_tier,
    s.coin0_claimed_amount,
    s.coin1_claimed_amount
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id;
