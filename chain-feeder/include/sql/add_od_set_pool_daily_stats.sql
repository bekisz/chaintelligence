-- Set-level LP daily aggregates (optional product lp.set.daily_stats).
-- per (set, pool, day), deriving from the universal pool daily stats via the
-- od_set_pool_member bridge. A shared pool is counted once per set/day.
CREATE TABLE IF NOT EXISTS od_set_pool_daily_stats (
    set_id     TEXT NOT NULL REFERENCES od_set(id),
    pool_id    INTEGER NOT NULL,
    day        DATE NOT NULL,
    tx_count   INTEGER NOT NULL DEFAULT 0,
    volume_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    tvl_usd    DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (set_id, pool_id, day)
);
