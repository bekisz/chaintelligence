-- ============================================================================
-- Route taxonomy: origin/destination pair, concrete route, hop edges, and a
-- daily per-route aggregate used by the fast (pre-aggregated) /api/routes/analyze
-- read path.
--
-- Apply via:  psql "$DATA_WAREHOUSE_DB" -f create_route_tables.sql
-- ============================================================================

BEGIN;

-- 1. Origin/destination pair: coarse identity of a route's endpoints.
CREATE TABLE IF NOT EXISTS origin_destination_pair (
    id              SERIAL PRIMARY KEY,
    chain_id        SMALLINT NOT NULL REFERENCES chain(id),
    origin_contract VARCHAR(64) NOT NULL,        -- lowercased token contract address
    dest_contract   VARCHAR(64) NOT NULL,        -- lowercased token contract address
    origin_coin_id  INTEGER REFERENCES coin(coin_id),   -- enrichment, may be NULL
    dest_coin_id    INTEGER REFERENCES coin(coin_id),
    origin_symbol   VARCHAR(10),
    dest_symbol     VARCHAR(10),
    first_seen      TIMESTAMPTZ,
    last_seen       TIMESTAMPTZ,
    UNIQUE (chain_id, origin_contract, dest_contract)
);

-- 2. Route: one specific path between a pair. Identity = ordered pool sequence.
CREATE TABLE IF NOT EXISTS route (
    route_id      SERIAL PRIMARY KEY,
    pair_id       INTEGER NOT NULL REFERENCES origin_destination_pair(id),
    chain_id      SMALLINT NOT NULL REFERENCES chain(id),
    hops          SMALLINT NOT NULL,
    canonical_key TEXT NOT NULL UNIQUE,          -- "{pair_id}:{pool1.id}:{pool2.id}:..."
    first_seen    TIMESTAMPTZ,
    last_seen     TIMESTAMPTZ
);

-- 3. Normalized graph edges for a route (ordered pool hops).
CREATE TABLE IF NOT EXISTS route_hop (
    route_id  INTEGER NOT NULL REFERENCES route(route_id) ON DELETE CASCADE,
    seq       SMALLINT NOT NULL,
    pool_id   INTEGER NOT NULL REFERENCES liquidity_pool(id),
    token_in  VARCHAR(64) NOT NULL,              -- lowercased token contract address
    token_out VARCHAR(64) NOT NULL,              -- lowercased token contract address
    PRIMARY KEY (route_id, seq)
);

-- 4. Per-day pre-aggregated stats per route (fast-path read model).
CREATE TABLE IF NOT EXISTS route_daily_stats (
    route_id   INT NOT NULL REFERENCES route(route_id) ON DELETE CASCADE,
    day        DATE NOT NULL,
    tx_count   INT NOT NULL DEFAULT 0,
    swap_count INT NOT NULL DEFAULT 0,
    volume_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (route_id, day)
);

CREATE INDEX IF NOT EXISTS idx_route_daily_stats_day ON route_daily_stats (day);

-- 5. Attribute every swap log to its route.
ALTER TABLE swaps ADD COLUMN IF NOT EXISTS route_id INT REFERENCES route(route_id);

-- Indexes for fast path + practical lookup.
CREATE INDEX IF NOT EXISTS idx_swaps_route ON swaps (route_id);
CREATE INDEX IF NOT EXISTS idx_route_hop_pool ON route_hop (pool_id);
CREATE INDEX IF NOT EXISTS idx_route_pair ON route (pair_id);

COMMIT;