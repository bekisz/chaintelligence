# PRD: Deprecate Zapper API — Transition to Native LP Ingestion

## 1. Background

Chaintelligence tracks user LP positions through three independent ingestion pathways:

| DAG | Source | Schedule | What it does |
|---|---|---|---|
| `zapper_lp_ingestion` | Zapper GraphQL API | `*/15 * * * *` | Discovers positions, ingests coins/pools/positions/snapshots, refreshes prices (CryptoCompare), backfills ranges (Graph/RPC), scans fee claims (RPC) |
| `graph_lp_ingestion` | The Graph subgraphs | `@hourly` | Discovers V3/V4 positions, ingests coins/pools/positions/snapshots. **Delegates** price refresh and range backfill to `zapper_lp_ingestion` via cross-DAG import |
| `rpc_lp_ingestion_v2` | On-chain RPC logs | manual trigger | Scans NFT transfer events to discover positions; enriches on-chain details |

Additionally, two other DAGs handle related concerns:
- `backfill_claims_history` (`@daily`) — runs `backfill_claims_rpc.py` as a BashOperator for incremental fee-claim scanning.
- `tiered_coin_price_ingestion` (`*/15 * * * *`) — refreshes coin prices via CoinMarketCap in tiered schedules.

The Zapper DAG has become the **monolith**: it is the only DAG that wires together position discovery + price refresh + range backfill + claim scanning into a single pipeline. The Graph DAG is functionally dependent on it via `from dags.zapper_lp_ingestion import ...`. Removing the Zapper DAG therefore requires **decomposing its responsibilities**, not merely deleting it.

## 2. Why Remove Zapper

1. **Proprietary dependency.** Zapper is a closed API with opaque schema changes, usage-based rate limits, and an auth header (`ZAPPER_AUTH_HEADER`) that has required manual renewal.
2. **Data quality issues.** Zapper returns display-oriented data: verbose token names ("SAVINGS USDS"), opaque `position_key` values, and image URLs that need heuristic mapping. The native Graph path returns structured on-chain data (token IDs, tick ranges, pool addresses) directly.
3. **Redundancy.** The Graph DAG + RPC DAG already cover the same position discovery. The Zapper path is the legacy bootstrap that has been superseded.
4. **Cross-DAG coupling.** `graph_lp_ingestion.py` imports task functions from `zapper_lp_ingestion.py` at runtime (`from dags.zapper_lp_ingestion import update_prices` / `fetch_missing_ranges`). This fragile coupling breaks if either DAG is renamed, paused, or deleted.

## 3. Scope: What the Zapper DAG Actually Does

The `zapper_lp_ingestion` DAG executes 7 task groups in sequence:

```
fetch_zapper_balances → ingest_coins → update_prices → ingest_pools → ingest_positions
                                                                          ↓
                                                              ┌── fetch_missing_ranges
                                                              └── ingest_snapshots → [claims_eth_v3, claims_eth_v4, ...]
```

| # | Task | Zapper-specific? | Replacement |
|---|---|---|---|
| 1 | `fetch_zapper_balances` | **Yes** — calls `zapper_client.fetch_zapper_data()` | Graph DAG's `discover_graph_positions()` already does this natively |
| 2 | `ingest_coins` | **Partially** — heuristic image mapping from Zapper `displayProps` | Graph DAG's `ingest_coins_data()` via `graph_ingestion_helpers.py` |
| 3 | `update_prices` | **No** — calls CryptoCompare; Zapper-independent | Extract to shared module or drop (see §4.1) |
| 4 | `ingest_pools` | **Partially** — uses `get_standard_pool_info()` with hardness sort | Graph DAG's `ingest_pools_data()` via `graph_ingestion_helpers.py` |
| 5 | `ingest_positions` | **Partially** — parses Zapper position_key format | Graph DAG's `ingest_positions_data()` |
| 6 | `fetch_missing_ranges` | **No** — queries DB then calls Graph/RPC fetchers | Extract to shared module |
| 7 | `ingest_snapshots` | **Partially** — maps Zapper asset/unclaimed arrays | Graph DAG's `ingest_snapshots_data()` |
| 8 | `update_claims_batch` | **No** — calls `backfill_claims_rpc.run_claims_scan()` | Already independently available via `backfill_claims_history` DAG |

**Key insight:** Tasks 3, 6, and 8 are Zapper-independent utilities that happen to live inside the Zapper DAG. Everything else has a native equivalent in the Graph pipeline.

## 4. Migration Design

### 4.1 Price Refresh: Drop from Graph DAG, Rely on Dedicated Price DAGs

The Zapper DAG's `update_prices` task calls CryptoCompare with a `PRICE_SYMBOL_MAPPING` (WETH→ETH, WBTC→BTC, etc.) to refresh the `coin.price` column. However:

- `tiered_coin_price_ingestion` already runs on `*/15 * * * *` and updates prices via CoinMarketCap (richer data: market cap, percent changes, TVL).
- The CryptoCompare-based `update_prices` is a legacy fallback providing only the `price` field.

**Decision:** Remove the CryptoCompare price refresh from the LP pipeline entirely. The `tiered_coin_price_ingestion` DAG is the canonical price source. If CryptoCompare coverage is still needed for niche tokens, it can be added as a fallback tier in that DAG later.

> [!WARNING]
> Verify that every coin symbol used in LP pools has a `cmc_id` and is covered by the tiered price DAG's target families. Coins without `cmc_id` will stop getting price updates.

### 4.2 Range Backfill: Extract to Shared Module

The `fetch_missing_ranges` task queries all positions with missing tick data and resolves them via Graph subgraphs or RPC. This is completely Zapper-independent.

**Action:** Move this logic to `chain-feeder/dags/common/tasks.py` (new file) so it can be imported by `graph_lp_ingestion.py` cleanly:

```python
# chain-feeder/dags/common/tasks.py
from airflow.sdk import task

@task
def fetch_missing_ranges():
    """Backfills tick_lower/tick_upper/current_tick for positions missing range data."""
    from include.uniswap_v3_range_fetcher import fetch_position_range_data
    from include.uniswap_v4_range_fetcher import fetch_v4_position_range_data
    from include.uniswap_v4_graph_fetcher import fetch_v4_position_range_data_from_graph
    # ... (existing logic from zapper_lp_ingestion.py lines 377–444)
```

### 4.3 Claims Scanning: Already Independent

The `update_claims_batch` tasks in the Zapper DAG are thin wrappers around `backfill_claims_rpc.run_claims_scan(network, protocol)`. This module is **already independently invoked** by the `backfill_claims_history` DAG (`@daily` schedule via BashOperator).

**Action:** No new code needed. After removing the Zapper DAG, claims scanning continues via `backfill_claims_history`. If the 15-minute frequency is desired (vs daily), update `backfill_claims_dag.py`'s schedule or add the parallelized batch tasks from the Zapper DAG into `graph_lp_ingestion.py`.

### 4.4 Position Key Reconciliation

Zapper-ingested positions use opaque keys like the Zapper API's `key` field or `"{protocol}-{label}-{network}-{address}"`. The Graph DAG uses deterministic keys like `"uniswapv3-{network}-{token_id}"`.

For **existing data**, both key formats may coexist in `liquidity_pool_position` for the same underlying NFT. After Zapper is removed, no new Zapper-keyed positions will be created, but stale Zapper-keyed rows may remain.

**Action:** Write a one-time migration script that:
1. Identifies Zapper-keyed positions that have a `token_id` matching a Graph-keyed position.
2. Reassigns any snapshots from the Zapper-keyed position to the Graph-keyed one.
3. Deletes the orphaned Zapper-keyed position rows.

### 4.5 Utility Function Consolidation

`normalize_symbol()` and `get_standard_pool_info()` are duplicated across three files:
- `chain-feeder/dags/zapper_lp_ingestion.py`
- `chain-feeder/dags/graph_lp_ingestion.py`
- `chain-feeder/include/graph_ingestion_helpers.py`

**Action:** Keep the canonical versions in `graph_ingestion_helpers.py` (which is already the shared module). Remove the duplicates from the DAG files.

### 4.6 Graph DAG Promotion

`graph_lp_ingestion.py` becomes the **primary LP ingestion DAG**:

| Change | Before | After |
|---|---|---|
| Schedule | `@hourly` | `*/15 * * * *` |
| Description | "Zapper alternative" | "Native LP position discovery and ingestion via The Graph" |
| `update_prices` task | Imports from zapper DAG | **Removed** (handled by `tiered_coin_price_ingestion`) |
| `backfill_ranges` task | Imports from zapper DAG | Imports from `common.tasks` |
| Claims | Not wired | **Optional:** wire `update_claims_batch` tasks, or rely on `backfill_claims_history` |
| Tags | `['defi', 'graph', 'native']` | `['defi', 'graph', 'lp']` |

## 5. Files Affected

### Delete (7 files)

| File | Reason |
|---|---|
| [zapper_client.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/zapper_client.py) | Zapper GraphQL client |
| [zapper_config.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/zapper_config.py) | Zapper credentials/endpoint |
| [zapper_lp_ingestion.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/zapper_lp_ingestion.py) | Zapper ingestion DAG |
| [zapper_lp_migration.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/zapper_lp_migration.py) | One-time migration DAG (already executed) |
| [zapper_etl_standalone.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/scripts/zapper_etl_standalone.py) | Standalone Zapper test script |
| [run_zapper_etl.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/scripts/run_zapper_etl.py) | Standalone Zapper CLI runner |
| [zapper-instructions.txt](file:///Users/szabi/git/chaintelligence/.agent/rules/zapper-instructions.txt) | 5500-line Zapper API schema reference (agent rule) |

### Create (2 files)

| File | Purpose |
|---|---|
| `chain-feeder/dags/common/tasks.py` | Shared tasks: `fetch_missing_ranges` extracted from zapper DAG |
| `chain-feeder/dags/migrate_zapper_position_keys.py` | One-time DAG to deduplicate Zapper vs Graph position keys |

### Modify (12 files)

| File | Changes |
|---|---|
| [graph_lp_ingestion.py](file:///Users/szabi/git/chaintelligence/chain-feeder/dags/graph_lp_ingestion.py) | Remove zapper imports, use `common.tasks`, update schedule/description/tags |
| [index.html](file:///Users/szabi/git/chaintelligence/web/static/index.html) | Update LP card text: "from Zapper snapshots" → "from on-chain data" |
| [.env.secrets.example](file:///Users/szabi/git/chaintelligence/.env.secrets.example) | Remove `ZAPPER_AUTH_HEADER` |
| [CLAUDE.md](file:///Users/szabi/git/chaintelligence/CLAUDE.md) | Remove Zapper client, DAG, secrets, and rule file references |
| [README.md](file:///Users/szabi/git/chaintelligence/README.md) | Remove `ZAPPER_AUTH_HEADER` from credentials table |
| [architecture.md](file:///Users/szabi/git/chaintelligence/docs/architecture.md) | Remove Zapper from diagram, data sources, DAG descriptions |
| [DAGS.md](file:///Users/szabi/git/chaintelligence/chain-feeder/docs/DAGS.md) | Remove `zapper_lp_ingestion` section and schedule entry |
| [SCHEMA.md](file:///Users/szabi/git/chaintelligence/chain-feeder/docs/SCHEMA.md) | Update `position_key` description: remove "provided by Zapper" |
| [kubernetes-migration.md](file:///Users/szabi/git/chaintelligence/docs/features/kubernetes-migration.md) | Remove `ZAPPER_AUTH_HEADER` from secrets table, Zapper LP from DAG lists |
| [lp-range-data-status.md](file:///Users/szabi/git/chaintelligence/docs/lp-range-data-status.md) | Update file references |
| [uniswap_v3_range_fetcher.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/uniswap_v3_range_fetcher.py) | Update docstring: "from Zapper" → "from DAG" |
| [uniswap_v4_graph_fetcher.py](file:///Users/szabi/git/chaintelligence/chain-feeder/include/uniswap_v4_graph_fetcher.py) | Update docstring: "from Zapper" → "from DAG" |

### Environment Variables

| Variable | Action |
|---|---|
| `ZAPPER_AUTH_HEADER` | Remove from `.env.secrets`, `.env.secrets.example`, K8s ExternalSecrets |
| `ZAPPER_ENDPOINT` | Remove (only referenced in `zapper_config.py`) |
| `TARGET_ADDRESS` | **Keep** — still used by `graph_lp_ingestion.py` and `rpc_lp_ingestion` |

## 6. Execution Order

> [!IMPORTANT]
> Each step must leave the system in a working state. Do not delete the Zapper DAG until all dependents are decoupled.

| Step | Action | Validates |
|---|---|---|
| 1 | Create `chain-feeder/dags/common/tasks.py` with `fetch_missing_ranges` | Module imports without error |
| 2 | Refactor `graph_lp_ingestion.py`: remove zapper imports, use shared tasks, update schedule to `*/15` | `airflow dags list` succeeds; DAG graph renders correctly |
| 3 | Verify `tiered_coin_price_ingestion` covers all LP-relevant coins | Query: `SELECT symbol FROM coin WHERE symbol NOT IN (SELECT cf.symbol FROM coin_family cf) AND price IS NOT NULL` |
| 4 | Verify `backfill_claims_history` runs independently | Trigger manually; check claim amounts update in snapshots |
| 5 | Create and run `migrate_zapper_position_keys.py` | No duplicate positions for same token_id |
| 6 | **Pause** `zapper_lp_ingestion` DAG (do not delete yet) | Monitor for 48 hours; verify Graph DAG populates snapshots |
| 7 | Delete Zapper files (7 files listed above) | `airflow dags list` still succeeds |
| 8 | Update documentation and UI (12 files) | Visual review |
| 9 | Remove `ZAPPER_AUTH_HEADER` from secrets | Deployment succeeds without it |

## 7. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Graph subgraph goes down | Position discovery stops | `rpc_lp_ingestion_v2` serves as fallback; add alerting on Graph DAG failures |
| Tiered price DAG doesn't cover niche tokens | `coin.price` goes stale for some assets → incorrect `balance_usd` | Pre-migration audit of coin coverage (Step 3) |
| Position key migration misses edge cases | Duplicate position rows | Run dedup query after migration; keep Zapper DAG paused (not deleted) for 48h rollback window |
| `backfill_claims_history` daily schedule is too infrequent | Claims detected hours later than before (was 15-min in Zapper DAG) | Optionally add parallelized claim tasks to `graph_lp_ingestion.py` |

## 8. Out of Scope

- **PancakeSwap support.** The Graph DAG currently only discovers Uniswap V3/V4. PancakeSwap pools are ingested via separate swap sync DAGs and are not affected.
- **Aerodrome / non-Uniswap LP protocols.** These were never discovered via Zapper's portfolio API in the current implementation (only Uniswap positions were parsed).
- **Snapshot backfill.** Historical snapshots created by the Zapper DAG are preserved as-is. No re-ingestion from The Graph is planned.
