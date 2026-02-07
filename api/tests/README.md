# Chaintelligence API Testing Guide

This directory contains automated tests for the Chaintelligence Portal API. These tests verify the health and correctness of the reorganized API endpoints.

## What is Tested?

The test suite (`test_api.py`) validates the following:

1. **Assets**:
    * `GET /api/coin/list`: Verifies the token metadata list is accessible without authentication.
    * `GET /api/coin/price-history`: Verifies historical price data retrieval for specific symbols (Public).
2. **Route Analytics**:
    * `GET /api/routes/date-range`: Verifies access control (401 without auth) and data retrieval (200 with auth) for swap data availability.
    * `GET /api/routes/analyze`: Verifies the complex route analysis logic and response structure (Auth required).
3. **Liquidity Pools**:
    * `GET /api/lp/position-summary`: Verifies retrieval of aggregated LP snapshots (Auth required).

## Prerequisites

Ensure you have the following installed:

* Python 3.8+
* `requests` library
* `python-dotenv` library

```bash
pip install requests python-dotenv
```

## How to Run the Tests

### 1. Configure Environment

The tests use environment variables for authentication. You can either export them in your shell or create a `.env` file in the project root:

```bash
export PORTAL_USERNAME=admin
export PORTAL_PASSWORD=chaintelligence
export API_URL=http://localhost:8000
```

### 2. Start the Server

Ensure the Chaintelligence server is running:

```bash
docker-compose up chaintelligence-server
```

### 3. Execute Tests

Run the test script from the root directory or the `tests` directory:

```bash
python routing-web/tests/test_api.py
```

## Troubleshooting

* **401 Unauthorized**: Check your `PORTAL_USERNAME` and `PORTAL_PASSWORD` settings.
* **Connection Refused**: Ensure the server is running and `API_URL` is correct.
* **No data for selected date range**: This usually indicates the database is empty or the `uniswap_v3_swaps` table hasn't been synced for the test period.
