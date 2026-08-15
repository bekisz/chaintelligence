-- Replace the surrogate `id` primary key on liquidity_pool_daily_stats with a
-- natural composite primary key (pool_id, date).
-- The old schema used `id SERIAL PRIMARY KEY` + `UNIQUE(pool_id, date)`, which
-- is redundant: the unique key is the real identity and the app never
-- references the surrogate id. Matches route_daily_stats (route_id, day).
-- Apply to an existing warehouse (after the table rename):
--   psql "$DATA_WAREHOUSE_DB" -f make_liquidity_pool_daily_stats_composite_pk.sql

DO $$
DECLARE
    cname text;
BEGIN
    -- Drop the old primary key (surrogate id). Constraint names may still be
    -- the pre-rename `liquidity_pool_history_pkey`, so look them up dynamically.
    SELECT conname INTO cname
    FROM pg_constraint
    WHERE conrelid = 'liquidity_pool_daily_stats'::regclass AND contype = 'p'
    ORDER BY conname LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE liquidity_pool_daily_stats DROP CONSTRAINT %I', cname);
    END IF;

    -- Drop the redundant UNIQUE (pool_id, date) constraint.
    SELECT conname INTO cname
    FROM pg_constraint
    WHERE conrelid = 'liquidity_pool_daily_stats'::regclass AND contype = 'u'
    ORDER BY conname LIMIT 1;
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE liquidity_pool_daily_stats DROP CONSTRAINT %I', cname);
    END IF;
END $$;

ALTER TABLE liquidity_pool_daily_stats DROP COLUMN IF EXISTS id;
ALTER TABLE liquidity_pool_daily_stats ADD PRIMARY KEY (pool_id, date);