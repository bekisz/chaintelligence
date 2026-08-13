-- Durable queue for asynchronous route classification.
-- Apply once to an existing warehouse. Included in init_db.sql for new installs.
CREATE TABLE IF NOT EXISTS route_classification_queue (
    tx_hash       TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    available_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at    TIMESTAMPTZ,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT route_classification_queue_status_check
        CHECK (status IN ('pending', 'processing', 'complete'))
);

CREATE INDEX IF NOT EXISTS idx_route_classification_queue_claim
    ON route_classification_queue (available_at, tx_hash)
    WHERE status IN ('pending', 'processing');
