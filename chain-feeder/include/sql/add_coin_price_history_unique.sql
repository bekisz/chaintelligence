-- Restore the UNIQUE(coin_id, timestamp) constraint on coin_price_history.
-- init_db.sql declares this constraint, but this instance is missing it, which
-- breaks the ON CONFLICT upserts used by the defillama_global_coin_price_history
-- DAG and by the /api/coin/price-history on-demand fetch. There are no duplicate
-- (coin_id, timestamp) rows, so adding the constraint is safe.
ALTER TABLE coin_price_history
    ADD CONSTRAINT coin_price_history_coin_id_timestamp_key UNIQUE (coin_id, timestamp);
