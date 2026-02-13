# Airflow DAGs Documentation

This document provides comprehensive documentation for all Airflow DAGs in the Chaintelligence data pipeline.

## Table of Contents

1. [Overview](#overview)
2. [Price & Metadata DAGs](#price--metadata-dags)
3. [Liquidity Pool DAGs](#liquidity-pool-dags)
4. [Event Backfill DAGs](#event-backfill-dags)
5. [Manual/Utility DAGs](#manualutility-dags)
6. [DAG Scheduling Summary](#dag-scheduling-summary)

---

## Overview

The Chaintelligence data pipeline consists of 14 active Airflow DAGs that handle:

- **Price feeds** and coin metadata synchronization
- **Liquidity pool** position tracking and historical data
- **Event backfilling** for claims and LP position changes
- **Manual utilities** for one-off syncs and fixes

All DAGs use the `chaintelligence_db` Postgres connection and are designed to be idempotent and resumable.

---

## Price & Metadata DAGs

### `coin_price_update_cmc`

**Purpose**: Primary price feed using CoinMarketCap API with integrated coin mapping sync.

**Schedule**: Every 15 minutes (`*/15 * * * *`)

**Key Features**:

- **3-Tier Price Update Strategy**:
  - **Tier 1 (Always)**: Coins in active LP positions
  - **Tier 2 (Conditional)**: Top 200 coins (every 30 min, configurable via `CMC_TIER2_INTERVAL_MINUTES`)
  - **Tier 3 (Conditional)**: Rank 200-500 (hourly, configurable via `CMC_TIER3_INTERVAL_MINUTES`)

- **Auto-Mapping Sync**: Refreshes coin metadata when:
  - Last sync > 7 days (checks `cmc_last_updated`)
  - Missing coins from top 1000 rankings

**Parameters**:

- `force_cmc_mapping` (boolean, default: false): Force mapping sync regardless of freshness

**Environment Variables**:

- `CMC_API_KEY`: CoinMarketCap API key
- `CMC_TIER2_INTERVAL_MINUTES`: Interval for top 200 coins (default: 30)
- `CMC_TIER3_INTERVAL_MINUTES`: Interval for rank 200-500 (default: 60)

**Updates**:

- `coin.price`, `coin.price_timestamp`
- `coin.percent_change_*`, `coin.market_cap`, `coin.tvl`
- `coin.cmc_id`, `coin.cmc_rank`, `coin.name`, `coin.slug`
- `coin.ethereum_address`, `coin.image_url`, `coin.decimals`
- Daily snapshot to `coin_price_history`

**Assets**:

- Produces: `postgres://postgres:5432/chaintelligence/public/coin`

**File**: [`coin_price_update_cmc.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/coin_price_update_cmc.py)

---

## Liquidity Pool DAGs

### `zapper_lp_ingestion`

**Purpose**: Ingest LP positions from Zapper API for tracked wallets.

**Schedule**: Every 30 minutes (`*/30 * * * *`)

**Key Features**:

- Fetches application balances (Uniswap V3, Aerodrome, etc.)
- Upserts positions to `liquidity_pool_position`
- Creates snapshots in `liquidity_pool_position_snapshot`
- Auto-creates pools in `liquidity_pool` if missing

**Environment Variables**:

- `ZAPPER_API_KEY`: Zapper API key

**Updates**:

- `liquidity_pool` (pools)
- `liquidity_pool_position` (positions)
- `liquidity_pool_position_snapshot` (snapshots)

**File**: [`zapper_lp_ingestion.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/zapper_lp_ingestion.py)

---

### `uniswap_v3_history_sync`

**Purpose**: Sync daily historical pool data (volume, TVL) from The Graph.

**Schedule**: Daily at 2 AM (`0 2 * * *`)

**Key Features**:

- Queries Uniswap V3 subgraph for pool day data
- Backfills up to 2 years of history on first run
- Incremental updates on subsequent runs
- Auto-creates pool entries if missing

**Updates**:

- `liquidity_pool_history` (daily metrics: `tx_count`, `volume_usd`)
- `liquidity_pool` (pool metadata if new)

**File**: [`uniswap_v3_history_sync.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/uniswap_v3_history_sync.py)

---

### `the_graph_uniswap_v3_swaps`

**Purpose**: Sync Uniswap V3 swap events for route analysis.

**Schedule**: Every 30 minutes (`*/30 * * * *`)

**Key Features**:

- Fetches recent swap events from The Graph
- Stores in `uniswap_v3_swap` table
- Powers route analysis and trading volume insights

**Updates**:

- `uniswap_v3_swap`

**Assets**:

- Produces: `postgres://postgres:5432/chaintelligence/public/uniswap_v3_swap`

**File**: [`the_graph_uniswap_v3_swaps.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/the_graph_uniswap_v3_swaps.py)

---

## Event Backfill DAGs

### `backfill_events_dag` (The Graph)

**Purpose**: Backfill LP position events (mints, burns, collects) using The Graph subgraph.

**Schedule**: Manual trigger only (no schedule)

**Key Features**:

- Queries position events from Uniswap V3 subgraph
- Processes mints, burns, and fee collections
- Stores in `liquidity_pool_position_event`
- Calculates balances and claimable fees

**Parameters**:

- Position filtering (wallet, token_id, etc.)

**Updates**:

- `liquidity_pool_position_event`

**File**: [`backfill_events_dag.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/backfill_events_dag.py)

---

### `backfill_events_rpc`

**Purpose**: Backfill LP position events using direct RPC/blockchain logs (fallback for The Graph).

**Schedule**: Manual trigger only (no schedule)

**Key Features**:

- Uses Web3 to query blockchain logs directly
- Handles IncreaseLiquidity, DecreaseLiquidity, Collect events
- More reliable than The Graph for recent/missing events
- Properly handles token decimals and amounts

**Updates**:

- `liquidity_pool_position_event`

**File**: [`backfill_events_rpc.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/backfill_events_rpc.py)

---

### `backfill_claims_dag` (The Graph)

**Purpose**: Backfill V3 claim events from The Graph.

**Schedule**: Manual trigger only (no schedule)

**Updates**:

- `liquidity_pool_position_event` (claim events)

**File**: [`backfill_claims_dag.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/backfill_claims_dag.py)

---

### `backfill_claims_rpc`

**Purpose**: Backfill V3 claim events using RPC (more reliable).

**Schedule**: Manual trigger only (no schedule)

**Key Features**:

- Direct blockchain log queries for `Transfer` events
- Detects NFT transfers to wallet (claims)
- More accurate event topic handling

**Updates**:

- `liquidity_pool_position_event` (claim events)

**File**: [`backfill_claims_rpc.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/backfill_claims_rpc.py)

---

## Manual/Utility DAGs

### `manual_pool_sync`

**Purpose**: Manual one-off pool metadata synchronization.

**Schedule**: Manual trigger only (no schedule)

**Key Features**:

- Fetches pool data from The Graph or RPC
- Updates pool metadata (fee tier, tokens, symbols)
- Useful for adding new pools or fixing metadata

**Updates**:

- `liquidity_pool`

**File**: [`manual_pool_sync.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/manual_pool_sync.py)

---

### `manual_tvl_sync`

**Purpose**: Manual TVL (Total Value Locked) synchronization for pools.

**Schedule**: Manual trigger only (no schedule)

**Updates**:

- `liquidity_pool.tvl` or related metrics

**File**: [`manual_tvl_sync.py`](file:///Users/szablocsbeki/git/chaintelligence/chain-feeder/dags/manual_tvl_sync.py)

---

## DAG Scheduling Summary

| DAG | Schedule | Interval | Primary Purpose |
|-----|----------|----------|----------------|
| `coin_price_update_cmc` | `*/15 * * * *` | 15 min | Price feeds + coin metadata |
| `zapper_lp_ingestion` | `*/30 * * * *` | 30 min | LP position tracking |
| `the_graph_uniswap_v3_swaps` | `*/30 * * * *` | 30 min | Swap event sync |
| `uniswap_v3_history_sync` | `0 2 * * *` | Daily (2 AM) | Historical pool metrics |
| `backfill_events_dag` | Manual | - | Event backfill (The Graph) |
| `backfill_events_rpc` | Manual | - | Event backfill (RPC) |
| `backfill_claims_dag` | Manual | - | Claim backfill (The Graph) |
| `backfill_claims_rpc` | Manual | - | Claim backfill (RPC) |
| `manual_pool_sync` | Manual | - | Pool metadata sync |
| `manual_tvl_sync` | Manual | - | TVL sync |

---

## Common Patterns

### Database Connection

All DAGs use `PostgresHook(postgres_conn_id='chaintelligence_db')` to connect to the database.

### Error Handling

- **Retries**: Most DAGs have `retries=1` with 3-5 minute retry delay
- **Idempotency**: All operations use `INSERT ... ON CONFLICT DO UPDATE` or similar patterns
- **Logging**: Comprehensive logging at INFO level for debugging

### Asset Definitions

DAGs that produce data assets define them using:

```python
asset_name = Asset("postgres://postgres:5432/chaintelligence/public/table_name")
```

This enables Airflow's data lineage tracking.

### Environment Variables

Sensitive keys (API keys, RPC URLs) are loaded from environment via:

- `.env` file (development)
- Docker Compose env vars (production)
- Airflow Variables (alternative)

Common env vars:

- `CMC_API_KEY`: CoinMarketCap API key
- `ZAPPER_API_KEY`: Zapper API key
- `WEB3_PROVIDER_URI`, `ETH_RPC_URL`: Ethereum RPC endpoints

---

## Debugging Tips

### Check DAG Logs

```bash
docker exec chaintelligence-airflow-scheduler airflow dags list
docker exec chaintelligence-airflow-scheduler airflow tasks test <dag_id> <task_id> <date>
```

### Trigger Manual DAG Run

```bash
airflow dags trigger <dag_id>
airflow dags trigger coin_price_update_cmc --conf '{"force_cmc_mapping": true}'
```

### View Task Instance Logs

Check Airflow UI → DAGs → [DAG Name] → Graph → [Task] → Log

### Force Refresh Asset

Delete from database and re-run:

```sql
DELETE FROM coin_price_history WHERE timestamp >= '2026-01-01';
```

---

## Related Documentation

- [Database Schema](SCHEMA.md) - Complete schema documentation
- [Architecture Overview](../docs/architecture.md) - System architecture
- [LP Range Data Status](../docs/lp-range-data-status.md) - LP position tracking details
