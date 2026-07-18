# Swap Ingestion Rules & Liquidity Pool Upsert Logic

> **Source of truth**: `dags/common/utils/uniswap_utils.py` — `PostgresStorage.save_swaps`

---

## Overview

Swaps are ingested per-batch from The Graph subgraph. Before any swap is written to the `swaps` table, it must pass a series of token and pool validation checks. If a matching pool does not yet exist, it is created on-the-spot. The whole batch is committed in a single transaction.

---

## Token Validation Rules

These checks run for **every swap** in the batch. A swap is **silently skipped** (no error) if any check fails.

### Rule 1 — Both token contract addresses must be known

The token lookup uses the **on-chain contract address** (lowercased), not the symbol. At the start of each batch, all rows from `coin_contract` for the target chain are loaded into a map:

```
LOWER(contract_address) → { coin_id, symbol, tracked }
```

If either `token0_address` or `token1_address` from the swap is **not present** in this map, the swap is **skipped**.

> This means: **only tokens whose contract address has been registered by CoinMarketCap** (via the `cmc_global_coin_metadata` DAG) will have their swaps ingested.

### Rule 2 — Both tokens must be tracked

Each entry in `coin_contract` has a `tracked` boolean (`DEFAULT TRUE`). If either token has `tracked = false`, the swap is **skipped**.

> Set `tracked = false` on a `coin_contract` row to suppress ingestion of all swaps involving that token, without removing it from the database.

### Rule 3 — Swap must have a valid ID

The subgraph swap `id` is required. If the `id` field is empty, the swap is skipped.

---

## Log Index Derivation

The `log_index` uniquely identifies a swap within a transaction (used as part of the `swaps` primary key alongside `ts` and `tx_hash`).

| Subgraph ID format | Parsing strategy |
|---|---|
| `{tx_hash}#{index}` (V3 style) | Split on `#`, parse right part as integer |
| `{something}-{index}` (V4/fallback) | `rsplit('-', 1)`, parse right part as integer |
| Neither | Fallback: monotonically incrementing counter per `tx_hash` within the batch |

---

## Pool Resolution & Upsert

After token validation, `save_swaps` tries to find the internal `liquidity_pool.id` for the swap using three strategies, in order:

### Step 1 — Match by on-chain pool ID (`pool_address` / `pool_id`)

```
sg_pool_id = swap['pool']['id']   # subgraph pool contract address (V3) or poolId hash (V4)
pool_id = pool_id_map.get(sg_pool_id.lower())
```

The map is loaded once per batch from `liquidity_pool.pool_id` for all existing pools.

### Step 2 — Match by token pair + fee

If Step 1 misses (pool_id not in DB), fall back to matching on:

```
(chain_id, protocol_id, frozenset({coin0_id, coin1_id}), fee_bps)
```

This handles cases where the pool exists in DB but was inserted without a `pool_id` (e.g. seeded manually or from an older run).

### Step 3 — Create pool on-the-spot (upsert)

If neither step finds a match, a new `liquidity_pool` row is inserted:

```sql
INSERT INTO liquidity_pool (chain_id, protocol_id, pool_name, fee_bps, coin0_id, coin1_id, pool_address, reverted)
VALUES (...)
ON CONFLICT (chain_id, protocol_id, pool_name, fee_bps, COALESCE(pool_id, ''))
DO UPDATE SET pool_address = COALESCE(liquidity_pool.pool_address, EXCLUDED.pool_address)
RETURNING id
```

**Pool name** is auto-generated as: `{SYMBOL0}-{SYMBOL1} {fee_tier}` (e.g. `WETH-USDC 0.05%`)

**Conflict key**: `(chain_id, protocol_id, pool_name, fee_bps, COALESCE(pool_id, ''))` — the unique index `idx_liquidity_pool_canonical`.

**On conflict**: only `pool_address` is updated, and only if it was previously NULL (i.e. the address from the subgraph wins over NULL, but never overwrites an existing address). All other fields are left unchanged.

The newly created pool is **cached in-memory** for the remainder of the batch to avoid redundant DB round-trips.

---

## Swap Insert (Deduplication)

Once a valid `pool_id` is resolved, the swap is appended to the batch insert:

```sql
INSERT INTO swaps (tx_hash, log_index, ts, pool_id, amount0, amount1, amount_usd)
VALUES (...)
ON CONFLICT (ts, tx_hash, log_index) DO NOTHING;
```

Duplicates (same `ts + tx_hash + log_index`) are silently ignored, making re-runs safe.

---

## Data Flow Summary

```
Subgraph batch
     │
     ▼
For each swap:
  ├─ token0_address in coin_contract? ──NO──► SKIP
  ├─ token1_address in coin_contract? ──NO──► SKIP
  ├─ token0.tracked = true?           ──NO──► SKIP
  ├─ token1.tracked = true?           ──NO──► SKIP
  ├─ swap.id present?                 ──NO──► SKIP
  │
  ├─ Find pool by pool_address/pool_id ──FOUND──► use it
  ├─ Find pool by (chain, protocol, tokens, fee_bps) ──FOUND──► use it
  └─ Create pool on-the-spot ──────────────────────────────────► INSERT + cache
     │
     ▼
  Append to batch → executemany INSERT INTO swaps ... ON CONFLICT DO NOTHING
     │
     ▼
  COMMIT (whole batch)
```

---

## Controlling What Gets Ingested

| Goal | How |
|---|---|
| Track a new token | Run `cmc_global_coin_metadata` DAG — it populates `coin_contract` with the CMC-verified address |
| Stop ingesting swaps for a token | `UPDATE coin_contract SET tracked = false WHERE contract_address = '0x...'` |
| Re-enable a token | `UPDATE coin_contract SET tracked = true WHERE contract_address = '0x...'` |
| Add a coin family / new coin | Add to `coin-families.yml`, run the CMC metadata DAG |
