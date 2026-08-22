-- ============================================================================
-- O&D control plane
--
-- Durable registry + coverage ledger for the declarative O&D architecture.
-- These tables are written by the coverageth and supporting workers, and read
-- by the reconciler planner (chain-feeder/include/reconcile.py) so it can
-- decide the correct action per (set product, chain, utc_day):
--   FETCH / CLASSIFY / MATERIALIZE / RESOLVE / UNAVAILABLE
--
-- Idempotent (CREATE ... IF NOT EXISTS); safe to apply repeatedly.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Declarative set registry (one row per compiled YAML set)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS od_set (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    origin       TEXT NOT NULL,
    dest         TEXT NOT NULL,
    bidirectional BOOLEAN NOT NULL DEFAULT TRUE,
    chains_all   BOOLEAN NOT NULL DEFAULT FALSE,
    chains       TEXT,               -- comma-separated chain names if not all
    version      INTEGER NOT NULL DEFAULT 1,
    config_path  TEXT,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- 2. Product registry (compiled from PRODUCTS in include/od_catalog.py)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS od_product (
    product_id           TEXT PRIMARY KEY,
    grain                TEXT NOT NULL,
    requires_classification BOOLEAN NOT NULL DEFAULT TRUE,
    durable              BOOLEAN NOT NULL DEFAULT TRUE,
    physical_table       TEXT NOT NULL,
    coverage_rule        TEXT NOT NULL DEFAULT 'present'
);

-- ---------------------------------------------------------------------------
-- 3. Set -> product requirements (window per product)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS od_set_product (
    set_id      TEXT NOT NULL REFERENCES od_set(id),
    product_id  TEXT NOT NULL REFERENCES od_product(product_id),
    window_spec JSONB,          -- normalized window spec (kind/days/start/end)
    PRIMARY KEY (set_id, product_id)
);

-- ---------------------------------------------------------------------------
-- 4. Resolved membership bridges (compiled per set)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS od_set_pair_member (
    set_id  TEXT NOT NULL REFERENCES od_set(id),
    pair_id BIGINT NOT NULL,
    PRIMARY KEY (set_id, pair_id)
);

CREATE TABLE IF NOT EXISTS od_set_route_member (
    set_id   TEXT NOT NULL REFERENCES od_set(id),
    route_id BIGINT NOT NULL,
    PRIMARY KEY (set_id, route_id)
);

CREATE TABLE IF NOT EXISTS od_set_pool_member (
    set_id  TEXT NOT NULL REFERENCES od_set(id),
    pool_id INTEGER NOT NULL,
    PRIMARY KEY (set_id, pool_id)
);

-- ---------------------------------------------------------------------------
-- 5. Coverage ledger (the decision inputs for the planner)
-- ---------------------------------------------------------------------------
-- source_day_coverage: a (chain, protocol) source is INGESTED for a utc_day
CREATE TABLE IF NOT EXISTS source_day_coverage (
    chain      VARCHAR(40) NOT NULL,
    protocol   VARCHAR(50) NOT NULL,
    utc_day    DATE NOT NULL,
    provider   VARCHAR(20) NOT NULL DEFAULT 'graph',
    status     TEXT NOT NULL DEFAULT 'INGESTED',  -- INGESTED | MISSING | REPLAY_FAILED
    event_count BIGINT,
    watermark  TIMESTAMPTZ,
    raw_purged_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chain_id, protocol, utc_day)
);

-- classification_day: raw swaps for (source,day) have been route-classified
CREATE TABLE IF NOT EXISTS classification_day_coverage (
    chain_id   VARCHAR(40) NOT NULL,
    utc_day    DATE NOT NULL,
    status     TEXT NOT NULL DEFAULT 'DONE',      -- PENDING | DONE | PARTIAL
    classified_count BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chain_id, utc_day)
);

-- product_day: a requested product is MATERIALIZED for (chain, utc_day)
CREATE TABLE IF NOT EXISTS product_day_coverage (
    product_id TEXT NOT NULL REFERENCES od_product(product_id),
    chain_id   VARCHAR(40) NOT NULL,
    utc_day    DATE NOT NULL,
    status     TEXT NOT NULL DEFAULT 'MATERIALIZED',
    rows_count BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (product_id, chain_id, utc_day)
);

-- ---------------------------------------------------------------------------
-- 6. Dirty facts (idempotent work queue for materializers)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dirty_route_day (
    route_id BIGINT NOT NULL,
    day      DATE NOT NULL,
    PRIMARY KEY (route_id, day)
);

CREATE TABLE IF NOT EXISTS dirty_pool_day (
    pool_id INTEGER NOT NULL,
    day     DATE NOT NULL,
    PRIMARY KEY (pool_id, day)
);