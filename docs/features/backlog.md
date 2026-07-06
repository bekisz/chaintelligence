# Backlog — Database & Performance

Items discussed but not yet implemented, ordered by estimated impact.

## 1. Materialized View for Hot Pairs

Pre-compute daily swap stats for popular pairs (USDC/USDT, WETH/USDC, etc.) so routing queries don't scan 20M rows every time.

```sql
CREATE MATERIALIZED VIEW token_pair_daily_stats AS
SELECT
    network,
    pair_key,
    date_trunc('day', ts) AS day,
    COUNT(*) AS swap_count,
    SUM(amount_usd) AS total_volume_usd
FROM swaps
WHERE (t0_coin_id, t1_coin_id) IN (
    (SELECT coin_id FROM coin WHERE symbol = 'USDC'),
    (SELECT coin_id FROM coin WHERE symbol = 'USDT')
)
GROUP BY network, pair_key, date_trunc('day', ts);
```

**Impact**: Near-instant routing queries for the most common token pairs.

## 2. Connection Pool Tuning

The current pool is 8 connections (`_POOL_MAXCONN` in `chain-feeder/routing/postgres_fetcher.py`). For concurrent users doing routing analysis, consider increasing to 16–32.

## 3. Add More Tracked Tokens to Coin Table

During Phase 2 migration, ~5903 token symbols were dropped because they weren't in the coin table. Adding key tokens (BNB ecosystem, popular memecoins, etc.) would increase swap coverage from 20M back toward 34M rows.

**Trade-off**: More rows = slower queries, but better coverage for routing analysis.

## 4. Server-Side Streaming for Routing API

`fetch_swaps_streaming()` exists with server-side cursors, but `main.py` still uses day-chunking with `fetch_swaps()`. Could stream the entire date range in one go instead of chunking by day.

## 5. `amount_usd >= 10.0` Threshold Revisit

The routing query filters `amount_usd >= 10.0`. For stablecoin pairs even $10 trades are meaningful, but this threshold also controls noise. Worth revisiting based on analysis volume.