# Route Analysis Performance Optimization Plan

## Executive Summary

The `/api/routes/analyze` endpoint was slow for token-pair queries across all networks due to:

1. Missing composite indexes for the common query patterns on the unified `swaps` table.
2. Per-chunk DB fetches blocking the event loop (fixed by offloading to worker threads).

## Changes Made

### 1. Database Indexes (`chain-feeder/include/sql/create_swaps_indexes.sql`)

Applied to the unified, monthly-partitioned `swaps` table (which superseded the legacy `uniswap_v2_swaps` / `uniswap_v3_swaps` / `uniswap_v4_swaps` tables — those now exist only as compatibility views, see `create_compatibility_views.sql`).

#### A. Token-Pair Composite Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_swaps_coin_pair_ts
    ON swaps (t0_coin_id, t1_coin_id, ts)
    INCLUDE (amount_usd, network, protocol, fee_bps, fee_display);

CREATE INDEX IF NOT EXISTS idx_swaps_coin1_0_ts
    ON swaps (t1_coin_id, t0_coin_id, ts)
    INCLUDE (amount_usd, network, protocol, fee_bps, fee_display);
```

The leading columns serve the token-pair filter (`t0_coin_id`/`t1_coin_id`); `ts` serves the date-range filter. The mirror index covers the reverse pair direction. `INCLUDE` columns let the planner evaluate the `amount_usd` and protocol/network filters from the index tuple before fetching the heap row.

These indexes support the query shape in `PostgresFetcher.fetch_swaps`:

```sql
SELECT s.tx_hash, s.log_index, s.ts, ch.name AS network, pr.name AS protocol, ...
FROM swaps s
JOIN liquidity_pool lp ON s.pool_id = lp.id
JOIN chain ch ON lp.chain_id = ch.id
JOIN protocol pr ON lp.protocol_id = pr.id
WHERE s.ts >= $1 AND s.ts <= $2 AND s.amount_usd >= 10.0
  [AND LOWER(ch.name) = $3]
  [AND (s.t0_coin_id = ANY(...) OR s.t1_coin_id = ANY(...))]
```

#### B. BRIN Indexes per Monthly Partition

```sql
CREATE INDEX IF NOT EXISTS swaps_2026_07_brin
    ON swaps_2026_07 USING BRIN (network, ts) WITH (pages_per_range = 32);
-- ...one per partition, plus swaps_default
```

BRIN indexes are tiny (~50 KB per partition) vs B-tree equivalents (~500 MB) and serve the network + timestamp range scans that span a full partition when no token-pair filter is present (the "all networks" case).

#### C. Planner Statistics

```sql
ANALYZE swaps;
```

### 2. Query & Execution Optimization (`api/routing/postgres_fetcher.py`)

- The fetcher queries the unified `swaps` table (joined to `liquidity_pool`, `chain`, `protocol`) rather than running separate V3/V4 queries with `UNION ALL`. The single-table design lets the planner use the composite indexes directly.
- Token filtering uses `coin_id` (integer FK) rather than string symbol comparison, which is faster and joins cleanly to `liquidity_pool`.

### 3. Streaming + Thread Offload (`api/main.py` — `/api/routes/analyze`)

The endpoint streams NDJSON and runs each chunk's DB fetch in a worker thread via `asyncio.to_thread`, so the event loop stays responsive and the frontend progress bar (`{"type":"progress","pct":...}`) updates smoothly. This was a fix for the UI sticking at 0% during long fetches.

## How to Apply

1. After the `swaps` table and its monthly partitions exist (`create_swaps_table.sql` + `migrate_swaps_data.sql`), apply the indexes:

```bash
psql "$DATA_WAREHOUSE_DB" -f chain-feeder/include/sql/create_swaps_indexes.sql
```

2. Indexes use plain `CREATE INDEX IF NOT EXISTS` (not `CONCURRENTLY`), so apply during a low-write window or accept a brief lock.

3. Statistics refresh is included at the end of the script (`ANALYZE swaps;`).

## Expected Performance Improvement

| Query Type | Before | After (estimated) |
|------------|--------|-------------------|
| Single-pair single network | 10-30s | 2-5s |
| Single-pair all networks | 20-60s | 5-15s |
| Other token pairs | 15-45s | 8-20s |

## Additional Optimization Opportunities (Future Work)

### Materialized View for Hot Pairs

Pre-compute daily stats for popular pairs:

```sql
CREATE MATERIALIZED VIEW token_pair_daily_stats AS
SELECT network, lp.id AS pool_id, date_trunc('day', s.ts) AS day,
       count(*), sum(s.amount_usd)
FROM swaps s
JOIN liquidity_pool lp ON s.pool_id = lp.id
GROUP BY network, lp.id, date_trunc('day', s.ts);
```

### Query Amount_USD Threshold

Consider the `amount_usd >= 10.0` filter — for stablecoins, even $10 trades are meaningful. May need adjustment based on analysis volume.

### Connection Pool Tuning

The current pool (`PostgresFetcher._get_pool`) uses a `ThreadedConnectionPool`. For heavy concurrent usage, consider increasing the max connections.

## Verification

After applying indexes, verify with EXPLAIN ANALYZE:

```sql
EXPLAIN ANALYZE
SELECT s.tx_hash, s.log_index, s.ts, s.amount_usd
FROM swaps s
WHERE s.ts >= '2026-07-01' AND s.ts <= '2026-07-02'
  AND (s.t0_coin_id = 1 OR s.t1_coin_id = 1);
```

Expected: Index Scan using `idx_swaps_coin_pair_ts` or `idx_swaps_coin1_0_ts`, falling back to a BRIN scan on the relevant partition when no token filter is supplied.
