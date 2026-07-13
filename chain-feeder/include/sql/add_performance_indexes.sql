-- ============================================================================
-- Add Performance Indexes
-- Implement Recommendation 1: Missing Indexes for positions and snapshots
-- Run via: docker exec -it chaintelligence-postgres-1 psql -U airflow -d chaintelligence -f /docker-entrypoint-initdb.d/add_performance_indexes.sql
-- ============================================================================

-- 1. Index on liquidity_pool_position(wallet_address)
-- Speeds up API queries that fetch all positions for a specific user wallet
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lpp_wallet ON liquidity_pool_position(wallet_address);

-- 2. Index on liquidity_pool_position(pool_id)
-- Speeds up JOINs between pool and position
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_lpp_pool_id ON liquidity_pool_position(pool_id);

-- 3. Composite Index on liquidity_pool_position_snapshot(position_id, timestamp DESC)
-- CRITICAL for performance. Speeds up finding the most recent snapshot for a position,
-- which prevents massive Full Table Scans when the API calls /api/lp/position-summary
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_snapshot_pos_time ON liquidity_pool_position_snapshot(position_id, timestamp DESC);

-- Optional: Index on timestamp for snapshot pruning or global time-series queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_snapshot_time ON liquidity_pool_position_snapshot(timestamp DESC);
