# Database Schema Documentation

## Overview

The schema is divided into four main tables:

1. **`coin`**: specific crypto asset with hardness (rank) and family. **Symbol (VARCHAR 8) is Primary Key**.
2. **`liquidity_pool`**: Stores static information about a Liquidity Pool (e.g., "ETH / USDC").
3. **`liquidity_pool_position`**: Stores static information about a user's position in a pool (e.g., Limits, Ticks, Token ID).
4. **`liquidity_pool_position_snapshot`**: Stores time-series data for a position. **Assets and Rewards are flattened**.

---

## Conventions

- **Case Insensitivity**: Coin symbols (`coin.symbol`, `liquidity_pool.coinX_symbol`) are enforcing uppercase VIA DATABASE TRIGGERS. All inserts are automatically converted to uppercase.
- **Symbol Constraint**: Coin symbols are truncated to 8 characters.
- **Ordering**: Liquidity Pools order their two assets based on Hardness. The "Harder" (more stable/standard) asset is always `coin1`.

---

## Tables

### 1. `coin`

Represents a unique crypto asset.

| Column | Type | Description |
| :--- | :--- | :--- |
| `symbol` | VARCHAR(8) (PK) | Ticker symbol (e.g., "USDC", "WETH"). Upper, Max 8 chars. |
| `hardness` | INTEGER | Hardness rank (Higher = Harder/More Stable). Used for ordering pairs. |
| `family` | VARCHAR(50) | Asset family (e.g., "USD", "EUR", "ETH", "BTC"). Default is the Symbol itself. |
| `image_url` | TEXT | URL to the token logo image. |

### 2. `liquidity_pool`

Represents a unique liquidity pool on a specific network and protocol.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | SERIAL (PK) | Unique Pool ID. |
| `network` | VARCHAR | Blockchain network (e.g., "Ethereum"). |
| `protocol` | VARCHAR | DEX Protocol (e.g., "Uniswap V3"). |
| `pool_name` | VARCHAR | Canonical name of the pool (e.g., "ETH / USDC"). |
| `fee_tier` | VARCHAR | Fee tier for the pool (e.g., "3000"). |
| `coin0_symbol` | VARCHAR(8) (FK) | Reference to `coin(symbol)`. |
| `coin1_symbol` | VARCHAR(8) (FK) | Reference to `coin(symbol)`. **Coin1 is strictly HARDER than Coin0**. |
| `created_at` | TIMESTAMP | Creation timestamp. |

**Ordering Rule**: `Coin1.hardness > Coin0.hardness`. Pairs are stored as [Softer] / [Harder] (e.g., ETH / USDC).

### 3. `liquidity_pool_position`

Represents a user's specific position within a pool.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | SERIAL (PK) | Unique Position ID. |
| `pool_id` | INT (FK) | Reference to `liquidity_pool`. |
| `position_key` | VARCHAR | Unique key provided by Zapper or generated. |
| `wallet_address` | VARCHAR | The wallet address owning the position. |
| `token_id` | VARCHAR | Uniswap V3 NFT Token ID. |
| `tick_lower` | INTEGER | Lower tick of the range. |
| `tick_upper` | INTEGER | Upper tick of the range. |
| `price_lower` | NUMERIC | Lower price of the range. |
| `price_upper` | NUMERIC | Upper price of the range. |
| `created_at` | TIMESTAMP | Creation timestamp. |

### 4. `liquidity_pool_position_snapshot`

Stores the historical state of a position at a specific point in time. Assets and Rewards are flattened as columns.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | SERIAL (PK) | Snapshot ID. |
| `position_id` | INT (FK) | Reference to `liquidity_pool_position`. |
| `timestamp` | TIMESTAMP | Time of data fetch. |
| `balance_usd` | NUMERIC | Total USD value of the position. |
| `coin0_amount` | NUMERIC | Amount of pool's Coin0 held. |
| `coin1_amount` | NUMERIC | Amount of pool's Coin1 held. |
| `coin0_claimable_amount` | NUMERIC | Amount of pool's Coin0 pending as fees (Unclaimed). |
| `coin1_claimable_amount` | NUMERIC | Amount of pool's Coin1 pending as fees (Unclaimed). |
| `coin0_claimed_amount` | NUMERIC | Cumulative amount of Coin0 fees collected (History). |
| `coin1_claimed_amount` | NUMERIC | Cumulative amount of Coin1 fees collected (History). |
| `current_tick` | INTEGER | Pool tick at snapshot. |
| `current_price` | NUMERIC | Pool price at snapshot. |
| `in_range` | BOOLEAN | In-range status. |
