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

## Usage Examples

### eth-USDC Pair with 15% APR
`file:///.../index.html?token1=eth&token2=usdc&apr=15`

### Custom Date Range
`file:///.../index.html?token1=btc&token2=usdt&start=2023-06-01&end=2023-12-31`

> [!NOTE]
> These parameters only affect the global controls in the first row. Individual strategy settings (ranges, rebalancing) are not currently saved in the URL to keep it readable and free of special characters.
