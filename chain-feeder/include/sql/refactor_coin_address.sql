-- Migration script to refactor coin table and price history
-- This version assumes coin_price_history might have been already refactored

-- 1. Cleanup dependencies
DELETE FROM coin_family WHERE symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL);

DELETE FROM liquidity_pool_history WHERE pool_id IN (
    SELECT id FROM liquidity_pool WHERE coin0_symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL)
    OR coin1_symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL)
);

DELETE FROM liquidity_pool_position_snapshot WHERE position_id IN (
    SELECT id FROM liquidity_pool_position WHERE pool_id IN (
        SELECT id FROM liquidity_pool WHERE coin0_symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL)
        OR coin1_symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL)
    )
);

DELETE FROM liquidity_pool_position WHERE pool_id IN (
    SELECT id FROM liquidity_pool WHERE coin0_symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL)
    OR coin1_symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL)
);

DELETE FROM liquidity_pool WHERE coin0_symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL)
OR coin1_symbol IN (SELECT symbol FROM coin WHERE ethereum_address IS NULL);

-- Finally delete coins that have no address
DELETE FROM coin WHERE ethereum_address IS NULL;

-- 2. Modify coin table
-- Ensure no duplicate addresses (even though CMC should provide unique ones, the symbols might differ)
DELETE FROM coin a USING coin b 
WHERE a.symbol > b.symbol AND LOWER(a.ethereum_address) = LOWER(b.ethereum_address);

ALTER TABLE coin ALTER COLUMN ethereum_address SET NOT NULL;

DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'coin_ethereum_address_key') THEN
        ALTER TABLE coin ADD CONSTRAINT coin_ethereum_address_key UNIQUE (ethereum_address);
    END IF;
END $$;

-- 3. Ensure coin_price_history is correct
-- Check if 'address' column exists. If 'symbol' still exists, migrate it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'coin_price_history' AND column_name = 'symbol') THEN
        -- Add address column if missing
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'coin_price_history' AND column_name = 'address') THEN
            ALTER TABLE coin_price_history ADD COLUMN address VARCHAR(42) REFERENCES coin(ethereum_address);
        END IF;

        -- Update address from symbol
        UPDATE coin_price_history h SET address = c.ethereum_address
        FROM coin c WHERE h.symbol = c.symbol;

        -- Drop symbol and constraints
        -- Note: this might need manual constraint dropping if names are unknown.
        -- But for simplicity we assume we can just drop the column.
        ALTER TABLE coin_price_history DROP COLUMN symbol;
        
        -- Enforce NOT NULL and UNIQUE on (address, timestamp)
        ALTER TABLE coin_price_history ALTER COLUMN address SET NOT NULL;
        ALTER TABLE coin_price_history ADD CONSTRAINT coin_price_history_address_timestamp_key UNIQUE (address, timestamp);
    END IF;
END $$;
