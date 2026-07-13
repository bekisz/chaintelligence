# Fetch TVL from EVM RPC Nodes

The Graph's TVL data for pools (especially stablecoin pools) has proven unreliable. The goal is to bypass The Graph for TVL by querying the token balances directly from the EVM RPC Nodes, calculating the USD value, and using that as the source of truth for TVL.

## Proposed Changes

### 1. New DAG: `rpc_tvl_sync.py`
A new Airflow DAG will be created to query TVL directly from EVM RPC nodes.
- **Query Pools**: Fetch all active pools (`pool_address`), along with their `coin0` and `coin1` details (`ethereum_address`, `decimals`) and their current USD prices from the database.
- **Multicall Balances**: Use `Multicall3` via the existing `RpcClient` to fetch `balanceOf(pool_address)` for both tokens in a batch to avoid rate limits.
- **Calculate TVL**: Convert the raw token balances to standard units using decimals, multiply by their respective USD prices, and sum them to get the total TVL in USD.
- **Upsert History**: Insert/Update `liquidity_pool_history` for the current date (`date = today()`) with the new `tvl_usd`.

### 2. Update Existing Graph DAGs
Files: `chain-feeder/dags/uniswap_v3_history_sync.py`, `chain-feeder/dags/uniswap_v4_history_sync.py`, `manual_tvl_sync.py`
- Modify the `ON CONFLICT DO UPDATE` clause when inserting into `liquidity_pool_history`.
- Instead of always overwriting `tvl_usd` with The Graph's data, we will use `COALESCE` or a conditional update so that if the existing `tvl_usd` was already updated by the RPC DAG for the current date, it will not be overwritten by The Graph.

## Open Questions

> [!IMPORTANT]
> **Historical TVL Data**:
> EVM RPC nodes can easily fetch the *current* token balances, which will fix the TVL for today and all future days. Fetching *historical* daily TVL (e.g. for the past 90 days) from RPC requires an Archive Node and is very slow/expensive. 
> **Are you okay with the RPC sync only fixing the TVL for the *current* date going forward?** The past historical TVL data will remain as whatever was fetched from The Graph.

> [!WARNING]
> **Token Prices**: 
> The RPC DAG will use the token prices currently available in the database to calculate the USD value of the TVL. Are the prices in the DB updated frequently enough for this?

