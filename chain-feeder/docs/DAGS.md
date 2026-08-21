# Chain-Feeder ETL Pipeline Architecture

This document provides a complete map of every ETL pipeline (Airflow DAG), its external data sources, the database tables it feeds, and how the pipelines connect to each other.

## System Overview

```mermaid
graph LR
    subgraph External["External Data Sources"]
        CMC["CoinMarketCap API"]
        DL["DefiLlama API"]
        GRF["The Graph<br/>Subgraphs"]
        RPC["EVM RPC Nodes<br/>(Eth, Arb, Base, BNB)"]
        YAML["coin-families.yml<br/>(Config File)"]
    end

    subgraph Pipelines["Airflow DAG Pipelines"]
        direction TB
        COIN["Coin<br/>Pipeline"]
        LP["LP Position<br/>Pipeline"]
        SWAP["Swap<br/>Pipeline"]
        HIST["History<br/>Pipeline"]
    end

    subgraph DB["PostgreSQL (chaintelligence)"]
        CT[("coin<br/>coin_contract<br/>coin_family<br/>coin_price_history")]
        LPT[("liquidity_pool<br/>liquidity_pool_daily_stats")]
        POS[("liquidity_pool_position<br/>liquidity_pool_position_snapshot<br/>liquidity_pool_position_event")]
        SW[("swaps")]
    end

    CMC --> COIN
    DL --> COIN
    YAML --> COIN
    COIN --> CT

    GRF --> LP
    RPC --> LP
    LP --> CT
    LP --> LPT
    LP --> POS

    GRF --> SWAP
    SWAP --> SW

    GRF --> HIST
    SW -.-> HIST
    RPC --> HIST
    HIST --> LPT
```

---

## Naming Convention

All DAGs follow: `<source>_<chain>_<protocol>_<version>_<output>_<fields>`

| Source | Values |
|--------|--------|
| `graph` | The Graph subgraph |
| `rpc` | EVM RPC node |
| `cmc` | CoinMarketCap API |
| `defillama` | DeFi Llama API |
| `yaml` | Static YAML config |

`<chain>` is `global` when the DAG spans all chains, or a specific network (`ethereum`, `arbitrum`, `base`, `bnb`). All DAGs set `catchup=False`.

---

## Pipeline Groups

### 1. Coin Pipeline — Metadata, Prices, Families

These DAGs maintain the `coin`, `coin_contract`, `coin_family`, and `coin_price_history` tables.

```mermaid
graph TD
    subgraph Triggers["Trigger Chain"]
        TIERED["cmc_global_coin_tiered_price<br/>⏰ */15 * * * *"]
        FAMILY["yaml_global_coin_family<br/>⏰ @weekly"]
        META["cmc_global_coin_metadata<br/>⏰ @weekly"]
        PRICE["cmc_global_coin_price<br/>⏰ manual (triggered)"]
        CPHF["defillama_global_coin_price_history<br/>⏰ 0 1 * * * (daily)"]
    end

    TIERED -- "triggers (with freshness check)" --> FAMILY
    TIERED -- "triggers per tier" --> PRICE
    FAMILY -- "triggers" --> META

    subgraph Sources["Data Sources"]
        CMC["CoinMarketCap API"]
        DL["DefiLlama API"]
        YAML["coin-families.yml"]
    end

    CMC --> META
    CMC --> PRICE
    DL --> CPHF
    YAML --> FAMILY

    subgraph Tables["Database Tables"]
        COIN_T["coin"]
        CC_T["coin_contract"]
        CF_T["coin_family"]
        CPH_T["coin_price_history"]
    end

    META --> COIN_T
    META --> CC_T
    FAMILY --> CF_T
    PRICE --> COIN_T
    CPHF --> CPH_T
```

| DAG | File | Schedule | Source | Tables Written |
|---|---|---|---|---|
| `cmc_global_coin_tiered_price` | [cmc_global_coin_tiered_price.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/cmc_global_coin_tiered_price.py) | `*/15 * * * *` | — (orchestrator) | — |
| `cmc_global_coin_metadata` | [cmc_global_coin_metadata.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/cmc_global_coin_metadata.py) | `@weekly` | CoinMarketCap (multi-source waterfall) | `coin`, `coin_contract` |
| `cmc_global_coin_price` | [cmc_global_coin_price.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/cmc_global_coin_price.py) | `None` (triggered) | CoinMarketCap | `coin` |
| `yaml_global_coin_family` | [yaml_global_coin_family.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/yaml_global_coin_family.py) | `@weekly` | `coin-families.yml` | `coin_family` |
| `defillama_global_coin_price_history` | [defillama_global_coin_price_history.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/defillama_global_coin_price_history.py) | `0 1 * * *` | DefiLlama | `coin_price_history` |

**Flow detail:**

1. **`cmc_global_coin_tiered_price`** is the primary scheduled orchestrator. Every 15 minutes it:
   - Triggers `yaml_global_coin_family` to sync families from YAML.
   - Checks 3 tiers of coins for price staleness (T1 = active LP coins, T2 = top 200, T3 = rank 200–500).
   - Triggers `cmc_global_coin_price` for each stale tier.

2. **`yaml_global_coin_family`** watches `coin-families.yml` for changes, then triggers `cmc_global_coin_metadata` (CMC mapping sync), then updates the `coin_family` table from YAML.

3. **`cmc_global_coin_metadata`** fetches the CoinMarketCap `/cryptocurrency/map` endpoint, resolves coin metadata (name, slug, rank, contract addresses), and upserts to `coin` and `coin_contract`.

4. **`cmc_global_coin_price`** resolves target symbols/addresses/families to CMC IDs, fetches quotes, and updates `coin.price`, `coin.price_timestamp`, percent changes, market cap, TVL, etc.

5. **`defillama_global_coin_price_history`** snapshots current coin prices to `coin_price_history` daily. Used for historical price analysis and APR calculations.

#### Multi-Source Contract Address Ingestion & Conflict Resolution

The `cmc_global_coin_metadata` DAG uses `MultiSourceContractEngine` ([contract_ingestion.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/contract_ingestion.py)) to discover and resolve smart contract addresses across multiple API providers.

##### Ingestion Waterfall
1. **Tier 1 (Manual Config):** Hardcoded overrides in `coin-families.yml` (`confidence_score: 100`).
2. **Tier 2 (CoinMarketCap API):** `cmc_map_fetch` & `cmc_info_fetch` tasks (`confidence_score: 90`).
3. **Tier 3 (On-Chain Swap Logs / Subgraphs):** Extracted on-the-fly from DEX swap events (`confidence_score: 85`).
4. **Tier 4 (CoinGecko API):** Query platform contract mappings for tokens unmapped by CMC (`confidence_score: 80`).
5. **Tier 5 (DexScreener API):** Query search API for DEX-native and newly launched tokens, filtering by pool liquidity ($ > $1,000) to reject scam/impostor tokens (`confidence_score: 70`).

##### Conflict Resolution Rule
When candidate contract addresses conflict or multiple sources return data for the same `(coin_id, chain_id)`:
```sql
INSERT INTO coin_contract (coin_id, chain_id, contract_address, decimals, is_native, source, confidence_score, verified_at, tracked)
VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), true)
ON CONFLICT (coin_id, chain_id) DO UPDATE SET
    contract_address = EXCLUDED.contract_address,
    decimals = COALESCE(EXCLUDED.decimals, coin_contract.decimals),
    is_native = EXCLUDED.is_native,
    source = EXCLUDED.source,
    confidence_score = EXCLUDED.confidence_score,
    verified_at = EXCLUDED.verified_at
WHERE EXCLUDED.confidence_score >= coin_contract.confidence_score;
```
- A lower-priority source (e.g. DexScreener score 70) **cannot overwrite** an address registered by a higher-priority source (e.g. CMC score 90).
- If a contract address is bound to another `coin_id` on the same chain (`idx_coin_contract_addr` unique index), it is updated only if the new incoming source has a higher confidence score.

---

### 2. Swap Pipeline — Trade Event Ingestion

These DAGs fetch swap (trade) events from The Graph subgraphs and write to a single **unified `swaps` table** (monthly range-partitioned on `ts`). The legacy per-protocol tables (`uniswap_v2_swaps`, `uniswap_v3_swaps`, `uniswap_v4_swaps`) still exist in the schema but only as compatibility views over `swaps` (see `create_compatibility_views.sql`); they are no longer written to directly.

```mermaid
graph TD
    subgraph Sources["Data Sources"]
        V2_SUB["Uniswap V2<br/>Subgraph"]
        V3_SUB["Uniswap V3<br/>Subgraphs"]
        V4_SUB["Uniswap V4<br/>Subgraphs"]
        AERO_SUB["Aerodrome Slipstream<br/>Subgraph"]
        PCS_SUB["PancakeSwap V3/V4<br/>Subgraphs"]
    end

    subgraph DAGs["Swap DAGs (@hourly)"]
        V2["graph_ethereum_uniswap_v2_swaps"]
        V3["graph_*_uniswap_v3_swaps<br/>(eth, arb, base, bnb)"]
        V4["graph_*_uniswap_v4_swaps<br/>(eth, arb, base, bnb)"]
        AERO["graph_base_aerodrome_v3_swaps"]
        PCS["graph_bnb_pancakeswap_v3_swaps<br/>graph_bnb_pancakeswap_v4_swaps"]
    end

    V2_SUB --> V2
    V3_SUB --> V3
    V4_SUB --> V4
    AERO_SUB --> AERO
    PCS_SUB --> PCS

    subgraph Tables["Database"]
        SWAPS["swaps<br/>(partitioned by month)"]
    end

    V2 --> SWAPS
    V3 --> SWAPS
    V4 --> SWAPS
    AERO --> SWAPS
    PCS --> SWAPS
```

| DAG | File | Schedule | Source | Networks |
|---|---|---|---|---|
| `graph_ethereum_uniswap_v2_swaps` | [graph_ethereum_uniswap_v2_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_ethereum_uniswap_v2_swaps.py) | `@hourly` | Uniswap V2 subgraph | Ethereum |
| `graph_ethereum_uniswap_v3_swaps` | [graph_ethereum_uniswap_v3_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_ethereum_uniswap_v3_swaps.py) | `@hourly` | Uniswap V3 subgraph | Ethereum |
| `graph_arbitrum_uniswap_v3_swaps` | [graph_arbitrum_uniswap_v3_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_arbitrum_uniswap_v3_swaps.py) | `@hourly` | Uniswap V3 subgraph | Arbitrum |
| `graph_base_uniswap_v3_swaps` | [graph_base_uniswap_v3_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_base_uniswap_v3_swaps.py) | `@hourly` | Uniswap V3 subgraph | Base |
| `graph_base_aerodrome_v3_swaps` | [graph_base_aerodrome_v3_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_base_aerodrome_v3_swaps.py) | `@hourly` | Aerodrome Slipstream subgraph | Base (protocol='Aerodrome') |
| `graph_bnb_uniswap_v3_swaps` | [graph_bnb_uniswap_v3_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_bnb_uniswap_v3_swaps.py) | `@hourly` | Uniswap V3 subgraph | BNB |
| `graph_bnb_pancakeswap_v3_swaps` | [graph_bnb_pancakeswap_v3_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_bnb_pancakeswap_v3_swaps.py) | `@hourly` | PancakeSwap V3 subgraph | BNB |
| `graph_ethereum_uniswap_v4_swaps` | [graph_ethereum_uniswap_v4_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_ethereum_uniswap_v4_swaps.py) | `@hourly` | Uniswap V4 subgraph | Ethereum |
| `graph_arbitrum_uniswap_v4_swaps` | [graph_arbitrum_uniswap_v4_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_arbitrum_uniswap_v4_swaps.py) | `@hourly` | Uniswap V4 subgraph | Arbitrum |
| `graph_base_uniswap_v4_swaps` | [graph_base_uniswap_v4_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_base_uniswap_v4_swaps.py) | `@hourly` | Uniswap V4 subgraph | Base |
| `graph_bnb_uniswap_v4_swaps` | [graph_bnb_uniswap_v4_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_bnb_uniswap_v4_swaps.py) | `@hourly` | Uniswap V4 subgraph | BNB |
| `graph_bnb_pancakeswap_v4_swaps` | [graph_bnb_pancakeswap_v4_swaps.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_bnb_pancakeswap_v4_swaps.py) | `@hourly` | PancakeSwap V4 subgraph | BNB |

**Flow detail:**

All swap DAGs follow the same pattern:
1. Fetch swap events from The Graph subgraphs for tracked token pairs, checkpointing against `MAX(ts)` per (network, protocol).
2. Resolve token symbols to `coin_id` foreign keys via `PostgresStorage.save_swaps`.
3. Insert to the unified `swaps` table (PK: `tx_hash, log_index`). Rows are distinguished by `protocol` and `network` columns.
4. Used by the History Pipeline for volume/TVL aggregation and by the API for route/trade analysis.

The `swaps` table uses monthly range partitioning on the `ts` column (e.g. `swaps_2026_07`). Schema defined in [create_swaps_table.sql](file:///Users/szabi/git/chaintelligence/chain-feeder/include/sql/create_swaps_table.sql). Retention is enforced by `ods_goal_state_retention` (see Utility DAGs; the older `config_global_swap_retention` is superseded).

Shared utilities live in [common/utils/uniswap_utils.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/common/utils/uniswap_utils.py) (`UniswapV3Fetcher`, `UniswapV4Fetcher`, `PostgresStorage`, etc.).

---

### 3. History Pipeline — Daily Pool Metrics Aggregation

These DAGs produce daily aggregated metrics (volume, TVL, tx count) per pool and write to `liquidity_pool_daily_stats` (auto-creating pool entries in `liquidity_pool` when needed).

```mermaid
graph TD
    subgraph Sources["Data Sources"]
        GRAPH_H["The Graph<br/>(Pool Day Data)"]
        SWAPS_T["swaps table"]
        RPC_TVL["EVM RPC<br/>(Multicall3 reserves)"]
    end

    subgraph DAGs["History DAGs"]
        V3H["graph_*_uniswap_v3_<br/>liquidity_pool_daily_stats<br/>⏰ 0 1 * * *"]
        V4H["graph_ethereum_uniswap_v4_<br/>liquidity_pool_daily_stats<br/>⏰ 0 1 * * *"]
        PCS["graph_bnb_pancakeswap_v4_<br/>liquidity_pool_daily_stats<br/>⏰ 0 1 * * *"]
        ROLLUP["global_liquidity_pool_<br/>history_rollup<br/>⏰ 0 2 * * *"]
        TVL["rpc_tvl_sync<br/>⏰ 0 3 * * *"]
    end

    GRAPH_H --> V3H
    GRAPH_H --> V4H
    SWAPS_T --> ROLLUP
    SWAPS_T --> PCS
    RPC_TVL --> TVL
    ROLLUP -.-> TVL

    subgraph Tables["Database Tables"]
        LP_T["liquidity_pool"]
        LPH_T["liquidity_pool_daily_stats"]
    end

    V3H --> LP_T
    V3H --> LPH_T
    V4H --> LP_T
    V4H --> LPH_T
    PCS --> LP_T
    PCS --> LPH_T
    ROLLUP --> LPH_T
    TVL --> LPH_T
```

| DAG | File | Schedule | Source | Tables Written | Networks |
|---|---|---|---|---|---|
| `graph_ethereum_uniswap_v3_liquidity_pool_daily_stats` | [graph_ethereum_uniswap_v3_liquidity_pool_daily_stats.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_ethereum_uniswap_v3_liquidity_pool_daily_stats.py) | `0 1 * * *` | The Graph (V3 pool day data) | `liquidity_pool`, `liquidity_pool_daily_stats` | Ethereum |
| `graph_arbitrum_uniswap_v3_liquidity_pool_daily_stats` | [graph_arbitrum_uniswap_v3_liquidity_pool_daily_stats.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_arbitrum_uniswap_v3_liquidity_pool_daily_stats.py) | `0 1 * * *` | The Graph (V3 pool day data) | `liquidity_pool`, `liquidity_pool_daily_stats` | Arbitrum |
| `graph_base_uniswap_v3_liquidity_pool_daily_stats` | [graph_base_uniswap_v3_liquidity_pool_daily_stats.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_base_uniswap_v3_liquidity_pool_daily_stats.py) | `0 1 * * *` | The Graph (V3 pool day data) | `liquidity_pool`, `liquidity_pool_daily_stats` | Base |
| `graph_ethereum_uniswap_v4_liquidity_pool_daily_stats` | [graph_ethereum_uniswap_v4_liquidity_pool_daily_stats.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_ethereum_uniswap_v4_liquidity_pool_daily_stats.py) | `0 1 * * *` | The Graph (V4 pool day data) | `liquidity_pool`, `liquidity_pool_daily_stats` | Ethereum |
| `graph_bnb_pancakeswap_v4_liquidity_pool_daily_stats` | [graph_bnb_pancakeswap_v4_liquidity_pool_daily_stats.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_bnb_pancakeswap_v4_liquidity_pool_daily_stats.py) | `0 1 * * *` | Swap tables (derived) + The Graph (pool IDs) | `liquidity_pool`, `liquidity_pool_daily_stats` | BNB |
| `global_liquidity_pool_daily_stats_rollup` | [global_liquidity_pool_daily_stats_rollup.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/global_liquidity_pool_daily_stats_rollup.py) | `0 2 * * *` | `swaps` (derived) | `liquidity_pool_daily_stats` | all |
| `rpc_tvl_sync` | [rpc_tvl_sync.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/rpc_tvl_sync.py) | `0 3 * * *` | EVM RPC (Multicall3 reserves) | `liquidity_pool_daily_stats` | all |

**Flow detail:**

1. **Per-network history syncs** query The Graph for `poolDayData` entities, auto-create missing pool entries in `liquidity_pool`, then upsert daily metrics into `liquidity_pool_daily_stats`.
2. **`global_liquidity_pool_daily_stats_rollup`** aggregates tx_count and USD volume from the `swaps` table into `liquidity_pool_daily_stats` for all pools, zero-fills dormant pools, and triggers the TVL-fallback backfill (`rpc_tvl_sync`). Runs daily at 2 AM.
3. **`rpc_tvl_sync`** reads on-chain reserves via Multicall3 / `eth_getStorageAt` (bypassing The Graph, which reports 0 TVL for stablecoin pools), computes USD TVL, and upserts into `liquidity_pool_daily_stats`. Runs daily at 3 AM, forward-fills 90 days.

---

### 4. LP Position Pipeline — Discovery, Snapshots, Events, Claims

These DAGs discover LP positions, create snapshots, scan for fee claims, and track position lifecycle events.

```mermaid
graph TD
    subgraph Sources["Data Sources"]
        GRAPH["The Graph<br/>(V3/V4 Subgraphs)"]
        RPC_N["EVM RPC Nodes"]
    end

    subgraph DAGs["DAGs"]
        SNAP["graph_all_uniswap_v3_<br/>liquidity_pool_position_snapshot<br/>⏰ */15 * * * *"]
        RPC_LP["rpc_ethereum_uniswap_v3_<br/>liquidity_pool_position<br/>⏰ hourly"]
        CLAIMS["rpc_all_uniswap_v3_<br/>liquidity_pool_position_snapshot_claims<br/>⏰ @daily"]
        EVENTS["rpc_all_uniswap_v3_<br/>liquidity_pool_position_event<br/>⏰ @daily"]
    end

    GRAPH --> SNAP
    RPC_N --> SNAP
    RPC_N --> RPC_LP
    RPC_N --> CLAIMS
    RPC_N --> EVENTS

    subgraph Tables["Database Tables"]
        COIN["coin"]
        POOL["liquidity_pool"]
        POS["liquidity_pool_position"]
        SNAP_T["liquidity_pool_position_snapshot"]
        EVT_T["liquidity_pool_position_event"]
    end

    SNAP --> COIN
    SNAP --> POOL
    SNAP --> POS
    SNAP --> SNAP_T
    RPC_LP --> POS
    CLAIMS --> SNAP_T
    CLAIMS --> POS
    EVENTS --> EVT_T
```

| DAG | File | Schedule | Source | Tables Written |
|---|---|---|---|---|
| `graph_all_uniswap_v3_liquidity_pool_position_snapshot` | [graph_all_uniswap_v3_liquidity_pool_position_snapshot.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_all_uniswap_v3_liquidity_pool_position_snapshot.py) | `*/15 * * * *` | The Graph V3/V4 subgraphs + RPC (range backfill) | `coin`, `liquidity_pool`, `liquidity_pool_position`, `liquidity_pool_position_snapshot` |
| `rpc_ethereum_uniswap_v3_liquidity_pool_position` | [rpc_ethereum_uniswap_v3_liquidity_pool_position.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/rpc_ethereum_uniswap_v3_liquidity_pool_position.py) | `timedelta(hours=1)` | EVM RPC (NFT Transfer logs) | `liquidity_pool_position` |
| `rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims` | [rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims.py) | `@daily` | EVM RPC (Collect/ModifyLiquidity logs) | `liquidity_pool_position_snapshot`, `liquidity_pool_position` |
| `rpc_all_uniswap_v3_liquidity_pool_position_event` | [rpc_all_uniswap_v3_liquidity_pool_position_event.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/rpc_all_uniswap_v3_liquidity_pool_position_event.py) | `@daily` | EVM RPC (IncreaseLiquidity, DecreaseLiquidity, Collect) | `liquidity_pool_position_event` |

**Flow detail:**

1. **`graph_all_uniswap_v3_liquidity_pool_position_snapshot`** is the primary position discovery pipeline:
   - Calls The Graph V3/V4 subgraphs to discover positions for `TARGET_ADDRESS` wallets across all chains.
   - Upserts coins, pools, positions, and creates time-series snapshots.
   - Backfills tick ranges (tick_lower, tick_upper, current_tick) via Graph or RPC.

2. **`rpc_ethereum_uniswap_v3_liquidity_pool_position`** is the on-chain fallback discovery:
   - Scans NFT Transfer events on the Uniswap V3/V4 NonfungiblePositionManager.
   - Enriches positions with on-chain details (token IDs, tick data).
   - Currently Ethereum-only; Arbitrum and Base are prepared but disabled.

3. **`rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims`** scans RPC logs for `Collect` and `ModifyLiquidity` events daily, backfilling claimable/claimed fees onto snapshots and positions.

4. **`rpc_all_uniswap_v3_liquidity_pool_position_event`** scans RPC logs for the full position lifecycle (IncreaseLiquidity, DecreaseLiquidity, Collect) and writes `liquidity_pool_position_event`.

---

### 5. Utility DAGs

| DAG | File | Schedule | Purpose | Tables Written |
|---|---|---|---|---|
| `ods_goal_state_retention` | [ods_goal_state_retention.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/ods_goal_state_retention.py) | `0 3 * * *` | Evaluates `config/ods-goal-state.yaml` requirements against the warehouse, reports coverage + gaps, backfills missing route daily stats, and (when `dry_run=false`) prunes rows outside the effective keep-windows across `swaps`, `route_daily_stats`, `route_daily_stats_bucket`, `liquidity_pool`, `liquidity_pool_daily_stats`, `liquidity_pool_daily_stats_bucket`. Replaces `config_global_swap_retention`. | `swaps` (deletions), `route_daily_stats` (delete+recompute), `route_daily_stats_bucket` (delete+recompute), `liquidity_pool_*` (deletions) |
| `ods_goal_state_backfill` | [ods_goal_state_backfill.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/ods_goal_state_backfill.py) | `*/30 * * * *` | **Converge the warehouse toward the O&D goal state** (also triggerable manually). Runs on a 30-min schedule so it keeps working until every requirement in `config/ods-goal-state.yaml` is met: (1) evaluate coverage/gaps, (2) recompute `route_daily_stats`/`route_daily_stats_bucket` from present swaps, (3) if gaps remain, trigger the per-chain swap ETL DAGs (`graph_*_swaps`) with a `backfill_days` conf for the networks with raw-`swaps` gaps plus the rollup DAGs. Only networks with actual gaps are re-fetched, so it stops querying The Graph once requirements are met. Param `backfill_days_cap` (default 90) caps how far back each network backfills. | none directly |
| `config_global_swap_retention` | [config_global_swap_retention.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/config_global_swap_retention.py) | `0 3 * * *` | **Superseded** by `ods_goal_state_retention` (paused on creation; kept for manual/emergency use). Reads `config/swap-retention.yaml` and deletes `swaps` rows older than the configured retention period per (network, protocol) in batches. | `swaps` (deletions) |

---

## Complete Database Schema Map

Shows which tables are **written** by which pipeline groups and **read** by which consumers.

| Table | Writers (DAGs) | Readers |
|---|---|---|
| `coin` | `cmc_global_coin_metadata`, `cmc_global_coin_price`, `graph_all_uniswap_v3_liquidity_pool_position_snapshot` | API (token registry) |
| `coin_contract` | `cmc_global_coin_metadata` | RPC claim/event scan, API |
| `coin_family` | `yaml_global_coin_family` | `cmc_global_coin_tiered_price`, `defillama_global_coin_price_history` |
| `coin_price_history` | `defillama_global_coin_price_history` | API (APR calculations) |
| `liquidity_pool` | history DAGs, `graph_all_uniswap_v3_liquidity_pool_position_snapshot` | API (pool analytics), swap DAGs (pool_id FK) |
| `liquidity_pool_daily_stats` | history DAGs, `global_liquidity_pool_daily_stats_rollup`, `rpc_tvl_sync` | API (pool analytics) |
| `liquidity_pool_position` | `graph_all_uniswap_v3_liquidity_pool_position_snapshot`, `rpc_ethereum_uniswap_v3_liquidity_pool_position`, `rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims` | API (`/api/lp/position-summary`) |
| `liquidity_pool_position_snapshot` | `graph_all_uniswap_v3_liquidity_pool_position_snapshot`, `rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims` | `v_lp_snapshots_summary` view |
| `liquidity_pool_position_event` | `rpc_all_uniswap_v3_liquidity_pool_position_event` | API (event timeline) |
| `swaps` | all swap DAGs | `global_liquidity_pool_daily_stats_rollup`, history DAGs, API (route/trade analysis) |
| `v_lp_snapshots_summary` | — (view) | API (`/api/lp/position-summary`) |

---

## Schedule Summary

| DAG | Schedule | Frequency | Pipeline |
|---|---|---|---|
| `cmc_global_coin_tiered_price` | `*/15 * * * *` | 15 min | Coin |
| `cmc_global_coin_metadata` | `@weekly` | weekly | Coin |
| `cmc_global_coin_price` | `None` (triggered) | — | Coin |
| `yaml_global_coin_family` | `@weekly` | weekly | Coin |
| `defillama_global_coin_price_history` | `0 1 * * *` | daily 1 AM | Coin |
| `graph_*_uniswap_v3_swaps` | `@hourly` | hourly | Swap |
| `graph_*_uniswap_v4_swaps` | `@hourly` | hourly | Swap |
| `graph_ethereum_uniswap_v2_swaps` | `@hourly` | hourly | Swap |
| `graph_base_aerodrome_v3_swaps` | `@hourly` | hourly | Swap |
| `graph_bnb_pancakeswap_v3_swaps` | `@hourly` | hourly | Swap |
| `graph_bnb_pancakeswap_v4_swaps` | `@hourly` | hourly | Swap |
| `graph_*_uniswap_v3_liquidity_pool_daily_stats` | `0 1 * * *` | daily 1 AM | History |
| `graph_ethereum_uniswap_v4_liquidity_pool_daily_stats` | `0 1 * * *` | daily 1 AM | History |
| `graph_bnb_pancakeswap_v4_liquidity_pool_daily_stats` | `0 1 * * *` | daily 1 AM | History |
| `global_liquidity_pool_daily_stats_rollup` | `0 2 * * *` | daily 2 AM | History |
| `rpc_tvl_sync` | `0 3 * * *` | daily 3 AM | History |
| `graph_all_uniswap_v3_liquidity_pool_position_snapshot` | `*/15 * * * *` | 15 min | LP |
| `rpc_ethereum_uniswap_v3_liquidity_pool_position` | `timedelta(hours=1)` | hourly | LP |
| `rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims` | `@daily` | daily | LP |
| `rpc_all_uniswap_v3_liquidity_pool_position_event` | `@daily` | daily | LP |
| `config_global_swap_retention` | `0 3 * * *` | daily 3 AM | Utility (superseded — paused) |
| `ods_goal_state_retention` | `0 3 * * *` | daily 3 AM | Utility |
| `ods_goal_state_backfill` | (manual) | on demand | Utility |

---

## Cross-DAG Dependencies

```mermaid
graph LR
    TIERED["cmc_global_coin_tiered_price"] -- "TriggerDagRunOperator" --> FAMILY["yaml_global_coin_family"]
    TIERED -- "TriggerDagRunOperator" --> CPRICE["cmc_global_coin_price"]
    FAMILY -- "TriggerDagRunOperator" --> CMETA["cmc_global_coin_metadata"]
    ROLLUP["global_liquidity_pool_daily_stats_rollup"] -- "triggers TVL backfill" --> TVL["rpc_tvl_sync"]
```

---

## Environment Variables

| Variable | Used By | Purpose |
|---|---|---|
| `CMC_API_KEY` | `cmc_global_coin_metadata`, `cmc_global_coin_price` | CoinMarketCap API authentication |
| `GRAPH_API_KEY` | swap DAGs, history DAGs, `graph_all_uniswap_v3_liquidity_pool_position_snapshot` | The Graph Gateway API key |
| `RPC_URL` | RPC LP DAGs, `rpc_tvl_sync` | Primary EVM RPC endpoint |
| `DATA_WAREHOUSE_DB` | all DAGs (via `include/settings.py`) | Direct psycopg2 connection string (authoritative) |
| `RPC_DISCOVERY_START_DATE` | `rpc_all_uniswap_v3_liquidity_pool_position_*` | Backfill start date |
| `SKIP_CLAIM_NETWORKS` | `rpc_all_uniswap_v3_liquidity_pool_position_snapshot_claims` | Networks to skip in claim scan |

---

## Shared Code Modules

| Module | Location | Used By |
|---|---|---|
| `uniswap_utils.py` | [common/utils/uniswap_utils.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/common/utils/uniswap_utils.py) | swap DAGs, history DAGs |
| `graph_ingestion_helpers.py` | [include/graph_ingestion_helpers.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/graph_ingestion_helpers.py) | `graph_all_uniswap_v3_liquidity_pool_position_snapshot` |
| `graph_discovery_client.py` | [include/graph_discovery_client.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/graph_discovery_client.py) | `graph_all_uniswap_v3_liquidity_pool_position_snapshot` |
| `rpc_discovery_engine.py` | [include/rpc_discovery_engine.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/rpc_discovery_engine.py) | RPC LP DAGs |
| `coinmarketcap_client.py` | [include/coinmarketcap_client.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/coinmarketcap_client.py) | `cmc_global_coin_metadata`, `cmc_global_coin_price` |
| `coin_family_resolver.py` | [include/coin_family_resolver.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/coin_family_resolver.py) | `yaml_global_coin_family`, `defillama_global_coin_price_history` |
| `contract_ingestion.py` | [include/contract_ingestion.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/contract_ingestion.py) | `cmc_global_coin_metadata` (MultiSourceContractEngine) |
| `v4_pool.py` | [include/v4_pool.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/v4_pool.py) | V4 swap/history DAGs, API (`api/main.py`) |
| `settings.py` | [include/settings.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/settings.py) | `data_warehouse_dsn()` — shared DSN derivation for both ETL and API configs; `load_distribution_config()` — global swap-size bucket params from `config/swap-distribution.yaml` |
