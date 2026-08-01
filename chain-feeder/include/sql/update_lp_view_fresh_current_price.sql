-- Prefer fresh coin-price ratios for v_lp_snapshots_summary.current_price.
--
-- The previous version used the stored snapshot current_price directly. That
-- value is only written during a range backfill (fetch_missing_ranges), so it
-- can be days old -- e.g. an EURC/USDC position showed 1.1362 while the live
-- EURC/USDC rate was ~1.152. The coin table is refreshed continuously by the
-- CMC feed, so the coin-price ratio is the freshest signal available.
--
-- Direction: the range fetchers display prices as token1-per-token0, inverted
-- when token0 is a stablecoin (or ETH/WETH against a non-stablecoin). That
-- convention is based on the ON-CHAIN token0/token1 ordering. pool.reverted
-- encodes how the DB coin0/coin1 ordering relates to the on-chain ordering, so
-- pool_display_price() reproduces the same display convention from coin prices.

CREATE OR REPLACE FUNCTION pool_display_price(
    p_reverted boolean,
    p_s0 text,
    p_s1 text,
    p_p0 numeric,
    p_p1 numeric
) RETURNS numeric
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN (
            -- token0 is a stablecoin while token1 is not  => invert
            (
                UPPER(CASE WHEN p_reverted THEN p_s1 ELSE p_s0 END) = ANY (
                    ARRAY['USDC','USDT','DAI','USDBC','EURC','EUROC','PYUSD','USDS','GHO','FRAX']
                )
                AND NOT (
                    UPPER(CASE WHEN p_reverted THEN p_s0 ELSE p_s1 END) = ANY (
                        ARRAY['USDC','USDT','DAI','USDBC','EURC','EUROC','PYUSD','USDS','GHO','FRAX']
                    )
                )
            )
            OR
            -- token0 is ETH/WETH and token1 is neither stablecoin nor ETH/WETH => invert
            (
                UPPER(CASE WHEN p_reverted THEN p_s1 ELSE p_s0 END) IN ('WETH','ETH')
                AND NOT (
                    UPPER(CASE WHEN p_reverted THEN p_s0 ELSE p_s1 END) = ANY (
                        ARRAY['USDC','USDT','DAI','USDBC','EURC','EUROC','PYUSD','USDS','GHO','FRAX']
                    )
                )
                AND NOT (UPPER(CASE WHEN p_reverted THEN p_s0 ELSE p_s1 END) IN ('WETH','ETH'))
            )
        )
        THEN
            -- inverted display: token1_price / token0_price
            CASE WHEN p_reverted THEN p_p0 / NULLIF(p_p1, 0) ELSE p_p1 / NULLIF(p_p0, 0) END
        ELSE
            -- plain display: token0_price / token1_price
            CASE WHEN p_reverted THEN p_p1 / NULLIF(p_p0, 0) ELSE p_p0 / NULLIF(p_p1, 0) END
        END
$$;

DROP VIEW IF EXISTS v_lp_snapshots_summary CASCADE;

CREATE VIEW v_lp_snapshots_summary AS
SELECT
    s.id,
    s."timestamp",
    pos.wallet_address AS address,
    pos.position_key,
    pr.name AS protocol,
    ch.name AS network,
    CASE
        WHEN pos.token_id IS NOT NULL
        THEN (pool.pool_name || ' (Token ID: ' || pos.token_id || ')')::VARCHAR
        ELSE pool.pool_name
    END AS position_label,
    s.balance_usd,
    c0.symbol AS asset0_symbol,
    s.coin0_amount AS asset0_amount,
    COALESCE(s.coin0_usd, 0) AS asset0_usd,
    c1.symbol AS asset1_symbol,
    s.coin1_amount AS asset1_amount,
    COALESCE(s.coin1_usd, 0) AS asset1_usd,
    c0.symbol AS reward0_symbol,
    s.coin0_claimable_amount AS reward0_amount,
    COALESCE(s.coin0_claimable_usd, 0) AS reward0_usd,
    c1.symbol AS reward1_symbol,
    s.coin1_claimable_amount AS reward1_amount,
    COALESCE(s.coin1_claimable_usd, 0) AS reward1_usd,
    jsonb_build_array(
        jsonb_build_object('symbol', c0.symbol, 'balance', s.coin0_amount, 'balanceUSD', COALESCE(s.coin0_usd, 0)),
        jsonb_build_object('symbol', c1.symbol, 'balance', s.coin1_amount, 'balanceUSD', COALESCE(s.coin1_usd, 0))
    ) AS assets,
    jsonb_build_array(
        jsonb_build_object('symbol', c0.symbol, 'balance', s.coin0_claimable_amount, 'balanceUSD', COALESCE(s.coin0_claimable_usd, 0)),
        jsonb_build_object('symbol', c1.symbol, 'balance', s.coin1_claimable_amount, 'balanceUSD', COALESCE(s.coin1_claimable_usd, 0))
    ) AS unclaimed,
    jsonb_build_array(c0.image_url, c1.image_url) AS images,
    COALESCE(s.coin0_claimable_usd, 0) + COALESCE(s.coin1_claimable_usd, 0) AS total_unclaimed_usd,
    pos.token_id,
    pos.tick_lower,
    pos.tick_upper,
    s.current_tick,
    pos.price_lower,
    pos.price_upper,
    -- Calculate current_price with a tiered fallback:
    -- 1. Fresh price derived from current coin prices (direction-corrected to
    --    match the price_lower/price_upper display convention).
    -- 2. Stored snapshot current_price (last range backfill).
    -- 3. Calculate from current_tick with decimal adjustment.
    COALESCE(
        pool_display_price(
            pool.reverted,
            c0.symbol,
            c1.symbol,
            c0.price,
            c1.price
        ),
        s.current_price,
        CASE
            WHEN s.current_tick IS NOT NULL THEN
                (POWER(1.0001, s.current_tick) *
                POWER(10, COALESCE(c1.decimals, 18) - COALESCE(c0.decimals, 18)))::NUMERIC
            ELSE NULL
        END
    ) AS current_price,
    s.in_range,
    CASE
        WHEN pool.fee_bps IS NULL THEN 'Dynamic'
        ELSE ((pool.fee_bps / 100.0)::text) || '%'
    END AS fee_tier,
    s.coin0_claimed_amount,
    s.coin1_claimed_amount
FROM liquidity_pool_position_snapshot s
JOIN liquidity_pool_position pos ON s.position_id = pos.id
JOIN liquidity_pool pool ON pos.pool_id = pool.id
JOIN chain ch ON pool.chain_id = ch.id
JOIN protocol pr ON pool.protocol_id = pr.id
JOIN coin c0 ON pool.coin0_id = c0.coin_id
JOIN coin c1 ON pool.coin1_id = c1.coin_id;
