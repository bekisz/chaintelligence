-- ============================================================================
-- Add Aerodrome-relevant tokens for the Base chain.
--
-- PostgresStorage.save_swaps (chain-feeder/dags/common/utils/uniswap_utils.py)
-- resolves t0/t1 coin_id via SYMBOL_TO_COIN_ID and skips any swap whose token
-- symbol is not in the coin table. For Aerodrome AERO pairs to be ingested,
-- AERO must exist in `coin` (with coin_id) and `coin_contract` (chain='base').
-- USDT and WBTC are also added for Base per the PRD's extended token list.
--
-- Addresses verified against the Aerodrome Slipstream subgraph
-- (GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM) via tokens(symbol_in:[...]).
--
-- NOTE: Base has multiple bridged USDT variants (the subgraph lists two:
-- 0x4fad8e51... and 0xfde4c96c...). coin_contract allows one address per
-- (coin_id, chain), and the fetcher queries one address per symbol, so only
-- the variant below is ingested. Add others by widening the fetcher's
-- per-symbol address handling if full USDT coverage is needed.
-- ============================================================================

-- 1. Seed the AERO coin (governance token, volatile → low hardness).
INSERT INTO coin (symbol, hardness) VALUES
  ('AERO', 600)
ON CONFLICT (symbol) DO NOTHING;

-- 2. Seed Base contract rows. USDT and WBTC symbols already exist in `coin`
--    (seeded for ethereum/arbitrum/bsc), so only coin_contract rows are added.
INSERT INTO coin_contract (coin_id, chain, contract_address, is_native) VALUES
  ((SELECT coin_id FROM coin WHERE symbol='AERO'), 'base', '0x940181a94a35a4569e4529a3cdfb74e38fd98631', FALSE),
  ((SELECT coin_id FROM coin WHERE symbol='USDT'), 'base', '0xfde4c96c8593536e31f229ea8f37b2ada2699bb2', FALSE),
  ((SELECT coin_id FROM coin WHERE symbol='WBTC'), 'base', '0x0555e30da8f98308edb960aa94c0db47230d2b9c', FALSE)
ON CONFLICT (coin_id, chain) DO UPDATE SET contract_address = EXCLUDED.contract_address;

-- 3. Backfill decimals for the new Base contracts (used by amount normalization).
UPDATE coin_contract SET decimals = 18
  WHERE chain = 'base' AND coin_id = (SELECT coin_id FROM coin WHERE symbol='AERO');
UPDATE coin_contract SET decimals = 6
  WHERE chain = 'base' AND coin_id = (SELECT coin_id FROM coin WHERE symbol='USDT');
UPDATE coin_contract SET decimals = 8
  WHERE chain = 'base' AND coin_id = (SELECT coin_id FROM coin WHERE symbol='WBTC');
