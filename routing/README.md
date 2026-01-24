# Uniswap V3 Routing Aggregation

A command-line tool to fetch and aggregate Uniswap V3 swap data for specified token pairs over a given time period.

## Overview

This application queries The Graph's Uniswap V3 subgraph to fetch swap events between the following tokens:

- AAVE
- LINK
- UNI
- PAXG
- USDC
- USDT
- WETH
- EURC
- EURCV
- WBTC

It aggregates transaction volumes and counts by token pairs (e.g., AAVE-LINK) and provides summary statistics.

## Installation

1. Navigate to the routing directory:

```bash
cd /Users/szablocsbeki/git/chaintelligence/routing
```

1. Install dependencies:

```bash
pip install -r requirements.txt
```

1. Set up The Graph API key (required):
   - Visit [The Graph Studio](https://thegraph.com/studio/)
   - Connect your wallet
   - Create a free API key (100,000 queries/month)
   - Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

- Edit `.env` and add your API key:

```bash
GRAPH_API_KEY=your_actual_api_key_here
```

## Usage

### Basic Examples

Get data for the last 7 days:

```bash
python main.py --days 7
```

Get data for a specific date range:

```bash
python main.py --start-date 2026-01-01 --end-date 2026-01-24
```

Get data for the last day:

```bash
python main.py --days 1
```

### Output Formats

Display as formatted table (default):

```bash
python main.py --days 7 --format table
```

Output as JSON:

```bash
python main.py --days 7 --format json
```

### Sorting Options

Sort by volume (default):

```bash
python main.py --days 7 --sort-by volume
```

Sort by transaction count:

```bash
python main.py --days 7 --sort-by tx_count
```

### Token Filtering

Filter to specific tokens only (useful for analyzing specific pairs):

```bash
# Check if there's any EURC-EURCV trading
python main.py --days 7 --tokens EURC EURCV

# Analyze stablecoin pairs
python main.py --days 7 --tokens USDC USDT EURC

# Focus on specific tokens with WETH
python main.py --days 7 --tokens AAVE LINK UNI WETH
```

Token symbols are case-insensitive. If you specify invalid tokens, the app will show you the list of valid options.

### Debugging

Enable verbose logging:

```bash
python main.py --days 1 --verbose
```

## CLI Parameters

| Parameter | Description | Required |
| --------- | ----------- | -------- |
| `--days N` | Number of days to look back from today | Yes* |
| `--start-date YYYY-MM-DD` | Start date for analysis | Yes* |
| `--end-date YYYY-MM-DD` | End date (defaults to today) | No |
| `--format {table,json}` | Output format | No (default: table) |
| `--sort-by {volume,tx_count}` | Sort pairs by metric | No (default: volume) |
| `--verbose` | Enable verbose logging | No |

*Either `--days` or `--start-date` must be provided

## Sample Output

```
================================================================================
Uniswap V3 Routing Analysis
Time Range: 2026-01-17 to 2026-01-24
================================================================================

SUMMARY
--------------------------------------------------------------------------------
Total Pairs              15
Total Volume (USD)       $12,345,678.90
Total Transactions       1,234
Avg Volume per Pair      $823,045.26
Avg Txs per Pair         82.3

TOKEN PAIRS (sorted by volume)
--------------------------------------------------------------------------------
+-------------+------------------+-----------+--------------+
| Pair        | Total Volume     | Tx Count  | Avg per Tx   |
+=============+==================+===========+==============+
| USDC-WETH   | $5,234,567.89    | 456       | $11,478.44   |
+-------------+------------------+-----------+--------------+
| USDT-WETH   | $3,456,789.12    | 345       | $10,019.97   |
+-------------+------------------+-----------+--------------+
| WBTC-WETH   | $2,345,678.90    | 234       | $10,024.23   |
+-------------+------------------+-----------+--------------+
...
```

## Architecture

- **config.py**: Token addresses and configuration
- **uniswap_fetcher.py**: GraphQL client for The Graph API
- **aggregator.py**: Data aggregation logic
- **main.py**: CLI interface

## Notes

- The application normalizes token pairs (AAVE-LINK and LINK-AAVE are treated as the same pair)
- Volumes are reported in USD for easier comparison across pairs
- The Graph API has a 1000 result limit per query; pagination is handled automatically
- Only swaps where both tokens are in the tracked list are included
