-- ============================================================================
-- Routing endpoint performance indexes
-- Target: /api/routes/analyze (fetch_swaps) and fetch_latest_prices
-- Apply manually against the data warehouse, e.g.:
--   psql "$DATA_WAREHOUSE_DB" -f chain-feeder/include/sql/add_routing_indexes.sql
--
-- All indexes are built CONCURRENTLY so the Airflow DAGs can keep writing to
-- the swap tables while the index is under construction. Each statement runs
-- in its own transaction (psql autocommit), which CONCURRENTLY requires.
-- ============================================================================

-- Drop the now-redundant plain (network, timestamp) indexes. The covering
-- indexes below have the same leading key columns plus INCLUDE columns, so
-- they satisfy every query the old ones did (and more). Removing the old
-- ones also stops the planner from picking the smaller non-covering index
-- over the covering one.
DROP INDEX CONCURRENTLY IF EXISTS idx_swaps_network_timestamp;
DROP INDEX CONCURRENTLY IF EXISTS idx_v4_swaps_network_timestamp;

-- Covering indexes for the main swap fetch.
-- Query shape:
--   WHERE timestamp >= $1 AND timestamp <= $2 AND amount_usd >= 10.0
--     [AND network = $3] [AND (token0_symbol = ANY OR token1_symbol = ANY)]
-- INCLUDE columns let the planner evaluate the amount_usd and token filters
-- from the index tuple before fetching the heap row, cutting heap reads
-- substantially (observed ~5x fewer heap page reads on a 2-day chunk).

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_swaps_net_ts_cover
  ON uniswap_v3_swaps (network, timestamp)
  INCLUDE (amount_usd, token0_symbol, token1_symbol);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_v4_swaps_net_ts_cover
  ON uniswap_v4_swaps (network, timestamp)
  INCLUDE (amount_usd, token0_symbol, token1_symbol);

-- Also keep a network-agnostic covering index for the "all chains" case
-- (no network filter), where the planner drives off timestamp alone.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_swaps_ts_cover
  ON uniswap_v3_swaps (timestamp)
  INCLUDE (amount_usd, network, token0_symbol, token1_symbol);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_v4_swaps_ts_cover
  ON uniswap_v4_swaps (timestamp)
  INCLUDE (amount_usd, network, token0_symbol, token1_symbol);

-- Supports fetch_latest_prices:
--   SELECT DISTINCT ON (c.symbol) c.symbol, h.price
--   FROM coin_price_history h JOIN coin c ON h.address = c.ethereum_address
--   [WHERE c.symbol = ANY($1)]
--   ORDER BY c.symbol, h.timestamp DESC
-- The scoped (symbols provided) path is already served by coin's primary key
-- plus the UNIQUE(address, timestamp) constraint; this index accelerates the
-- unscoped path (used by ShortcutFinder) and any symbol-driven access.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_coin_price_symbol_ts
  ON coin_price_history (symbol, timestamp DESC);

-- ============================================================================
-- fetch_pool_stats (the "Querying pool stats & APRs..." phase)
-- After the rewrite, pool stats are aggregated by pool_id with a date range:
--   WHERE pool_id = ANY(...) AND date BETWEEN $start AND $end
-- and a TVL fallback:
--   SELECT DISTINCT ON (pool_id) ... ORDER BY pool_id, date DESC
-- The UNIQUE(pool_id, date) constraint already serves both (confirmed via
-- EXPLAIN ANALYZE: Bitmap Index Scan on liquidity_pool_history_pool_id_date_key),
-- so no new index is needed here. We only DROP the now-redundant single-column
-- idx_lp_history_pool, since (pool_id, date) covers every pool_id lookup it did
-- (the constraint's leading column is pool_id). This also reduces write overhead
-- on the history table.
-- ============================================================================

DROP INDEX CONCURRENTLY IF EXISTS idx_lp_history_pool;

-- ============================================================================
-- Additional indexes for token-symbol queries (USDT-USDC and other pairs)
-- These complement the network/timestamp indexes by adding symbol-based
-- filtering support for the token_filter parameter in fetch_swaps.
-- ============================================================================

-- Composite index supporting: WHERE (token0_symbol = ANY OR token1_symbol = ANY) AND timestamp >= X AND timestamp <= Y
-- The planner can use this for any token pair query, not just stablecoins.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_swaps_symbol_ts_cover
  ON uniswap_v3_swaps (token0_symbol, token1_symbol, timestamp)
  INCLUDE (amount_usd, network, tx_hash, fee_tier, protocol);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_v4_swaps_symbol_ts_cover
  ON uniswap_v4_swaps (token0_symbol, token1_symbol, timestamp)
  INCLUDE (amount_usd, network, tx_hash, fee_tier, protocol);

-- ============================================================================
-- Partial indexes for stablecoin pairs - the most common routing queries
-- ============================================================================
-- Stablecoin list: USDC, USDT, DAI, USDS, USDE, EURC, EURQ, EUR, FDUSD, GHO, SUSDS
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_swaps_stablecoin_cover
  ON uniswap_v3_swaps (network, timestamp)
  INCLUDE (amount_usd, token0_symbol, token1_symbol, tx_hash, fee_tier, protocol)
  WHERE (LOWER(token0_symbol) IN ('usdc', 'usdt', 'dai', 'usds', 'usde', 'eurc', 'eurq', 'eur', 'fdusd', 'gho', 'susds')
     AND LOWER(token1_symbol) IN ('usdc', 'usdt', 'dai', 'usds', 'usde', 'eurc', 'eurq', 'eur', 'fdusd', 'gho', 'susds'));

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_v4_swaps_stablecoin_cover
  ON uniswap_v4_swaps (network, timestamp)
  INCLUDE (amount_usd, token0_symbol, token1_symbol, tx_hash, fee_tier, protocol)
  WHERE (LOWER(token0_symbol) IN ('usdc', 'usdt', 'dai', 'usds', 'usde', 'eurc', 'eurq', 'eur', 'fdusd', 'gho', 'susds')
     AND LOWER(token1_symbol) IN ('usdc', 'usdt', 'dai', 'usds', 'usde', 'eurc', 'eurq', 'eur', 'fdusd', 'gho', 'susds'));

-- ============================================================================
-- Index for coin_price_history lookups (fetch_latest_prices)
-- ============================================================================
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_coin_price_address_ts
  ON coin_price_history (address, timestamp DESC);
