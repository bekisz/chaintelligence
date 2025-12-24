# URL Parameters Documentation

The Liquidity Pool Backtester supports several URL parameters to allow for deep linking and sharing of specific configurations. These parameters are automatically updated in the URL as you modify the global controls.

## Supported Parameters

| Parameter | Description | Example |
| :--- | :--- | :--- |
| `token1` | The symbol of the target/base asset (e.g., eth, btc). | `token1=eth` |
| `token2` | The symbol of the quote asset (e.g., usdc, usdt). | `token2=usdc` |
| `apr` | The estimated annual percentage rate (APR) for the -50% to +100% range. | `apr=20` |
| `start` | The start date for the backtest in YYYY-MM-DD format. | `start=2023-01-01` |
| `end` | The end date for the backtest in YYYY-MM-DD format. | `end=2024-12-24` |

## Strategy Parameters

Strategies use a structured key format: `strategy[n].property`. Indices start from 1.

| Parameter | Description |
| :--- | :--- |
| `strategy[n].name` | Custom name for the strategy. |
| `strategy[n].range.min` | Minimum range % for the LP position. |
| `strategy[n].range.max` | Maximum range % for the LP position. |
| `strategy[n].rebalance` | Enable rebalancing (`true` or `false`). |
| `strategy[n].rebalance.range.min` | Rebalance trigger threshold (min %). |
| `strategy[n].rebalance.range.max` | Rebalance trigger threshold (max %). |
| `strategy[n].rebalance.delay` | Rebalance delay in days. |

## Usage Examples

### Multiple Strategies
`?token1=eth&strategy[1].name=Wide&strategy[1].range.min=-50&strategy[1].range.max=100&strategy[2].name=Narrow&strategy[2].range.min=-10&strategy[2].range.max=10`

### eth-USDC with 20% APR and Rebalancing
`?token1=eth&token2=usdc&apr=20&strategy[1].name=RebStrategy&strategy[1].rebalance=true&strategy[1].rebalance.delay=7`

### eth-USDC with 20% APR and Rebalancing with custom rebalance range
`file:///Users/szablocsbeki/git/chaintelligence/lp-backtester/index.html?token1=ETH&token2=USDC&apr=20&strategy[1].name=Current&strategy[1].rebalance=true&strategy[1].rebalance.delay=12&strategy[1].rebalance.range.min=-25&strategy[1].rebalance.range.max=22`

> [!TIP]
> This hierarchical format makes the URL readable and easy to modify manually for advanced testing.
