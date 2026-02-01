-- View to provide a one-row-per-position summary of LP snapshots
CREATE OR REPLACE VIEW v_lp_snapshots_summary AS
SELECT 
    id,
    timestamp,
    address,
    position_key,
    protocol,
    network,
    position_label,
    balance_usd,
    -- Flattened assets (assuming up to 2 for common pools)
    assets->0->>'symbol' as asset0_symbol,
    (assets->0->>'balance')::NUMERIC as asset0_amount,
    (assets->0->>'balanceUSD')::NUMERIC as asset0_usd,
    assets->1->>'symbol' as asset1_symbol,
    (assets->1->>'balance')::NUMERIC as asset1_amount,
    (assets->1->>'balanceUSD')::NUMERIC as asset1_usd,
    -- Flattened rewards
    unclaimed->0->>'symbol' as reward0_symbol,
    (unclaimed->0->>'balance')::NUMERIC as reward0_amount,
    (unclaimed->0->>'balanceUSD')::NUMERIC as reward0_usd,
    unclaimed->1->>'symbol' as reward1_symbol,
    (unclaimed->1->>'balance')::NUMERIC as reward1_amount,
    (unclaimed->1->>'balanceUSD')::NUMERIC as reward1_usd,
    -- Keep original JSON columns for app compatibility
    assets,
    unclaimed,
    images,
    -- Extract total rewards for sorting/summary
    COALESCE((SELECT SUM((u->>'balanceUSD')::NUMERIC) FROM jsonb_array_elements(unclaimed) u), 0) as total_unclaimed_usd,
    -- Range data
    token_id,
    tick_lower,
    tick_upper,
    current_tick,
    price_lower,
    price_upper,
    current_price,
    in_range,
    fee_tier
FROM lp_snapshots;
