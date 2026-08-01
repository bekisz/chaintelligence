---
name: test-reporter
description: Run the API test suite and generate a formatted terminal report. Use when the user asks to run tests and see a summary, generate a test report, or check test results with configuration details.
---

# Test Reporter

Generates a formatted test report for the Chaintelligence API test suite.

## Usage

```bash
python api/tests/test_report.py
```

### Options

| Flag | Description |
|------|-------------|
| `-t <pattern>` | Run a specific test (e.g. `-t test_20`) |
| `--env KEY=value` | Override test env vars (repeatable) |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POOL_MAX_CHECK` | 100 | Number of top pools to check for Uniswap links |
| `POOL_UNISWAP_WORKERS` | 5 | Parallel workers for Uniswap gateway API |
| `PCS_MAX_CHECK` | 100 | Number of PancakeSwap pools to check |

### Examples

```bash
# Full test suite with default config
python api/tests/test_report.py

# Check 500 Uniswap pools with 10 workers
python api/tests/test_report.py --env POOL_MAX_CHECK=500 --env POOL_UNISWAP_WORKERS=10

# Run only PancakeSwap test
python api/tests/test_report.py -t test_21

# Run from inside Docker
docker exec chaintelligence-server python api/tests/test_report.py
```

## Report format

The report includes:
- Run timestamp and duration
- Test configuration (env vars)
- Pass/fail/skip summary
- Per-test result table
- Failure details with root cause annotations (known issues like health degradation are flagged automatically)
