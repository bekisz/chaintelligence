-- Add columns to liquidity_pool_position to store the state at the time of range fetching
ALTER TABLE liquidity_pool_position ADD COLUMN IF NOT EXISTS current_tick INTEGER;
ALTER TABLE liquidity_pool_position ADD COLUMN IF NOT EXISTS current_price NUMERIC;

-- Update the view to prefer the position's current price (last fetched) over the snapshot's (which is currently NULL)
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
    
    -- Assets
    pool.coin0_symbol as asset0_symbol,
    s.coin0_amount as asset0_amount,
    0 as asset0_usd, 
    pool.coin1_symbol as asset1_symbol,
    s.coin1_amount as asset1_amount,
    0 as asset1_usd,
    
    -- Rewards
    pool.coin0_symbol as reward0_symbol,
    s.coin0_claimable_amount as reward0_amount,
    0 as reward0_usd,
    pool.coin1_symbol as reward1_symbol,
    s.coin1_claimable_amount as reward1_amount,
    0 as reward1_usd,

    -- JSONs
    jsonb_build_array(
        jsonb_build_object('symbol', pool.coin0_symbol, 'balance', s.coin0_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', pool.coin1_symbol, 'balance', s.coin1_amount, 'balanceUSD', 0)
    ) as assets,
    
    jsonb_build_array(
        jsonb_build_object('symbol', pool.coin0_symbol, 'balance', s.coin0_claimable_amount, 'balanceUSD', 0),
        jsonb_build_object('symbol', pool.coin1_symbol, 'balance', s.coin1_claimable_amount, 'balanceUSD', 0)
    ) as unclaimed,
     
    -- Images
    jsonb_build_array(
        (SELECT image_url FROM coin WHERE symbol = pool.coin0_symbol),
        (SELECT image_url FROM coin WHERE symbol = pool.coin1_symbol)
    ) as images,
    
    -- Total unclaimed (Calculated based on implied price from Balance USD)
    -- Handle Stablecoin as Token0 (Inverted Price P = Price(T1)/Price(T0))
    -- Normal Case (P = Price(T0)/Price(T1))
    CASE 
        WHEN s.balance_usd > 0 THEN
            CASE 
                -- Inverted Case: T0 is Stable, T1 is not. P = Price(T1)/Price(T0).
                -- Formula: V * (C0 + C1*P) / (A0 + A1*P)
                WHEN UPPER(pool.coin0_symbol) IN ('USDC', 'USDT', 'DAI', 'USDBC', 'USDB', 'EUROC', 'EURC') 
                     AND UPPER(pool.coin1_symbol) NOT IN ('USDC', 'USDT', 'DAI', 'USDBC', 'USDB', 'EUROC', 'EURC') THEN
                    (s.balance_usd * (s.coin0_claimable_amount + s.coin1_claimable_amount * COALESCE(s.current_price, pos.current_price, 0))) / 
                    NULLIF(s.coin0_amount + s.coin1_amount * COALESCE(s.current_price, pos.current_price, 0), 0)
                
                -- Standard Case: P = Price(T0)/Price(T1).
                -- Formula: V * (C0*P + C1) / (A0*P + A1)
                ELSE
                    (s.balance_usd * (s.coin0_claimable_amount * COALESCE(s.current_price, pos.current_price, 0) + s.coin1_claimable_amount)) / 
                    NULLIF(s.coin0_amount * COALESCE(s.current_price, pos.current_price, 0) + s.coin1_amount, 0)
            END
        ELSE 0 
    END as total_unclaimed_usd,
    
    -- Range data (Source from POS, not Snapshot for static/fetched fields)
    pos.token_id,
    pos.tick_lower,
    pos.tick_upper,
    -- Use stored current tick/price from position fetch
    COALESCE(s.current_tick, pos.current_tick) as current_tick,
    pos.price_lower,
    pos.price_upper,
    COALESCE(s.current_price, pos.current_price) as current_price,
    
    -- Calculate In Range dynamically
    CASE 
        WHEN COALESCE(s.current_tick, pos.current_tick) IS NOT NULL AND pos.tick_lower IS NOT NULL 
        THEN (COALESCE(s.current_tick, pos.current_tick) >= pos.tick_lower AND COALESCE(s.current_tick, pos.current_tick) <= pos.tick_upper)
        ELSE NULL 
    END as in_range,
    
    pool.fee_tier
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id;
