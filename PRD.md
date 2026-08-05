# Product Requirements Document (PRD)
## Counterfactual Uniswap V4 Pool Replay & Experiment Overlay Engine

---

## 1. Executive Summary & Objective

**Chaintelligence** is expanding its analytics capabilities to allow users to simulate the deployment of a fictional Uniswap V4 liquidity pool over historical time windows and evaluate its market impact block-by-block.

### Primary Use Case
A user wants to simulate introducing a new **Uniswap V4 tBTC/WBTC pool** on Ethereum with:
- **Fee Tier:** 0.08% (8 bps / 800 Uniswap fee units)
- **Initial Capital:** $10,000 USD
- **Position Strategy:** Single user-defined concentrated liquidity range
- **Competitor Target:** Under-cutting dominant existing pools (e.g., Pool 9417: V3 1-bp, Pool 9657: V3 5-bps, Pool 11740: V4 5-bps)

### Key Questions Answered
1. How much trading volume would be captured by this fictional pool?
2. What fee revenue, utilization rate, and APR would the fictional pool generate?
3. How much volume is displaced from existing direct competitor pools?
4. How are adjacent, multi-hop swap routes affected when tBTC or WBTC is used as an intermediate hop?

---

## 2. Replay Universe & Scope Constraints

- **Network:** Ethereum Mainnet (`chain_id = 1`) only.
- **Protocols Supported in Routing Universe:** Uniswap V3 and hookless, fixed-fee Uniswap V4 pools.
- **Excluded Protocols:** Uniswap V2, dynamic-fee V4, hooked V4, non-Uniswap DEXes/aggregators.
- **Fictional Candidate Pool Model:**
  - Protocol: Uniswap V4
  - Pair: exact Ethereum contract addresses (`tBTC` + `WBTC`)
  - Fee: 800 fee units (8 bps / 0.08%)
  - Capital: $10,000 initial liquidity at deployment
  - Range: Single user-defined price range (`tickLower` to `tickUpper`)
  - Position Management: Static initial position (no auto-rebalancing in V1)
- **Competitor Pool State:** Existing real pools retain their actual historical TVL, tick liquidity distribution, and observed state transitions.

---

## 3. Verified Live Data & Target Pool Baseline

Verification against the live database confirms the following target pools on Ethereum:

| Pool ID | Protocol | Pair | Fee | Observed Contract / ID | Historical Volume (Warehouse) |
|---:|---|---|---:|---|---|
| **9417** | Uniswap V3 | tBTC / WBTC | 1 bp (0.01%) | `0x73a38006d23517a1d383c88929b2014f8835b38b` | ~$85.4M (9,560 swaps) |
| **9657** | Uniswap V3 | tBTC / WBTC | 5 bps (0.05%) | `0xdbac78be00503d10ae0074e5e5873a61fc56647c` | ~$2.05M (236 swaps) |
| **11740** | Uniswap V4 | tBTC / WBTC | 5 bps (0.05%) | `0xef3b67cac5803a942dea79fa44069378258bc65f579c5b91804a7f7390d7290a` | ~$24.1K (112 swaps) |

### Verified Token Contracts (Ethereum)
- **tBTC (`coin_id` 8560):** `0x18084fba666a33d37592fa2633fd49a74dd93a88` (18 decimals)
- **WBTC (`coin_id` 2):** `0x2260fac5e5542a773aa44fbcfedf7c193bc2c599` (8 decimals)

---

## 4. Architecture & System Design

### A. Shared Real vs. Fictional API Overlay Strategy
Existing public endpoints remain real-world/baseline by default. Fictional simulations are attached via an `experiment=<uuid>` query parameter overlaying immutable, pre-computed simulation run outputs.

```text
GET /api/pools/search?...                          -> Factual pool history & APR
GET /api/pools/search?...&experiment=<exp_id>      -> Factual pools + synthetic candidate pool & delta metrics

GET /api/routes/analyze?...                        -> Observed historical routes
GET /api/routes/analyze?...&experiment=<exp_id>    -> Counterfactual route selection & volume displacement
```

### B. Persistence Schema (Immutable Experiment Models)
Experiments and replay runs are append-only to preserve analytical integrity and auditability. Synthetic records are **never** written into factual warehouse tables (`swaps`, `liquidity_pool_history`, `liquidity_pool`).

1. `counterfactual_experiment`:
   - `experiment_id` (UUID PK)
   - `name`, `description`
   - `definition` (JSONB): candidate pool config, token contracts, fee, initial capital, price bounds, range ticks
   - `definition_sha256` (SHA256 of canonical definition)
   - `code_version`
   - `created_at`
2. `counterfactual_experiment_run`:
   - `run_id` (UUID PK)
   - `experiment_id` (FK)
   - `status` (`queued` | `running` | `completed` | `failed`)
   - `source_manifest` (JSONB): block range, exact DB state snapshot checksum, RPC node ID
   - `started_at`, `finished_at`
3. `counterfactual_pool_metric`:
   - `(experiment_id, run_id, pool_id, metric_date)` (PK)
   - `volume_usd`, `tvl_usd`, `tx_count`, `fee_bps`, `apr`
   - `baseline` (JSONB): pre-simulation factual values
4. `counterfactual_experiment_result`:
   - `(experiment_id, run_id, result_scope, entity_key)` (PK)
   - Stores route displacement payloads, candidate pool daily metrics, and performance metrics.

---

## 5. Router-Faithful Replay Engine Requirements

### Block-by-Block Execution Flow
For every block in the user-selected period:
1. Reconstruct historical pool states (`sqrtPriceX96`, active liquidity, tick bitmap) for all eligible V3/V4 pools immediately prior to each block.
2. Inject candidate V4 pool initialized with $10,000 USD distributed across the user-configured price range at the opening block price.
3. For each historical swap transaction:
   - Decode input token, output token, amount, and constraints.
   - Quote route options across the baseline graph vs. counterfactual graph (including the synthetic 8-bp V4 pool).
   - Select the lowest-cost execution route under the versioned `uniswap-ethereum-v1` router policy (considering fees, gas, and price impact).
   - Update pool state, ticks, and liquidity on the selected route.
   - Accrue fees to the candidate pool position if traversed.
4. Record daily metrics: candidate volume, captured fees, APR, utilization rate, in-range duration, and per-pool volume displacement.

---

## 6. RPC Requirements & Technical Gaps Identified

A feasibility spike identified key technical gaps in the current infrastructure required for block-by-block replay:

1. **Archive Node Requirement:**
   - Block-by-block replay requires historical `eth_call`, `eth_getStorageAt`, and `eth_getLogs` at specific historical block numbers.
   - Standard pruned RPC nodes return errors for historical block tags.
2. **Missing Raw Blockchain Tables:**
   - Existing `swaps` table stores Graph-aggregated data without `block_number`, `transaction_index`, or raw event logs.
   - Required new tables: `chain_block`, `chain_transaction`, `chain_log`, `dex_pool_event`, `dex_pool_checkpoint`.
3. **Router Calldata Decoding:**
   - Need decoder modules for V3 `SwapRouter`/`SwapRouter02` and V4 `UniversalRouter` calldata.

---

## 7. Implementation Roadmap & Milestones

### Phase 1: RPC Infrastructure & Data Layer Spike (Current Step)
- Configure and verify credentialed Ethereum Archive JSON-RPC provider.
- Build raw block/log ingestion tables for target pool 9417 (tBTC/WBTC 1-bp).
- Reconstruct V3 pool 9417 state (`sqrtPriceX96`, liquidity, ticks) over 100 historical blocks and verify against archive `eth_call`.

### Phase 2: Counterfactual Execution Core
- Build exact Uniswap V3/V4 swap math engine (tick traversal, price impact, fee accrual).
- Build candidate V4 pool initializer ($10k capital -> V4 liquidity $L$ & tick bounds).
- Build router quote evaluation engine (`uniswap-ethereum-v1` policy).

### Phase 3: Experiment API & Persistence
- Create migration `create_counterfactual_experiment_tables.sql`.
- Implement `POST /api/experiments`, `POST /api/experiments/{id}/runs`, `GET /api/experiments/{id}/results`.
- Add `?experiment=<uuid>` overlay support to `/api/pools/search` and `/api/routes/analyze`.

### Phase 4: UI Integration
- Add "Fictional V4 Pool Simulation" controls on the `/backtester` portal.
- Display candidate KPI cards (Volume, Fees, APR, Utilization, In-Range %).
- Render pool volume displacement charts (Direct competitor impact vs. Multi-hop route impact).
