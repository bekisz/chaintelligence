ALTER TABLE liquidity_pool ADD COLUMN IF NOT EXISTS pool_address VARCHAR(42);
CREATE INDEX IF NOT EXISTS idx_lp_pool_address ON liquidity_pool(pool_address);
