-- ============================================================================
-- Harden route classification queue: generation counter + claim token.
--
-- Race being fixed: a producer can requeue a tx (new leg arrives from another
-- protocol) while a worker is classifying the older value. The worker's
-- unconditional completion update could then overwrite the newer pending state,
-- losing the late leg.
--
-- Fix: every producer requeue increments `generation`. The worker reads the
-- generation it claimed (`claim_token`) and only marks the row complete with a
-- conditional UPDATE ... WHERE generation = claim_token. If a requeue bumped
-- generation, the worker's completion no-ops, leaving the row pending.
-- ============================================================================

ALTER TABLE route_classification_queue
    ADD COLUMN IF NOT EXISTS generation BIGINT NOT NULL DEFAULT 0;

ALTER TABLE route_classification_queue
    ADD COLUMN IF NOT EXISTS claim_token BIGINT;