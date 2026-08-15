# Chaintelligence API Tests

Automated test suite for the Chaintelligence Portal API.

## Prerequisites

- API server running (via Docker or locally)
- Python 3.x
- `requests` library

## Running Tests

### Option 1: Inside Docker Container (Recommended)

The Docker container has all dependencies pre-installed:

```bash
# Run all tests
docker exec chaintelligence-server python api/tests/test_api.py -v

# Run specific test
docker exec chaintelligence-server python api/tests/test_api.py TestChaintelligenceAPI.test_06_price_by_cmc_id_single -v

# Run only price-by-cmc-id tests
docker exec chaintelligence-server python api/tests/test_api.py \
  TestChaintelligenceAPI.test_06_price_by_cmc_id_single \
  TestChaintelligenceAPI.test_07_price_by_cmc_id_multiple \
  TestChaintelligenceAPI.test_08_price_by_cmc_id_invalid \
  TestChaintelligenceAPI.test_09_price_by_cmc_id_too_many \
  TestChaintelligenceAPI.test_10_price_by_cmc_id_missing -v
```

### Generate a Markdown Report

Use the report runner whenever you want the test results saved to a file. It runs
the same API test suite and creates `api/tests/test-report.md` automatically.
Each row includes the test's full name, its docstring description, and its
result.

```bash
# Run all tests and create api/tests/test-report.md
docker exec chaintelligence-server python api/tests/test_report.py

# Run one test and create a report for that run
docker exec chaintelligence-server python api/tests/test_report.py -t test_20

# Choose a different report path
docker exec chaintelligence-server python api/tests/test_report.py \
  --report /tmp/api-test-report.md
```

### Option 2: Locally

If running tests from your local machine:

```bash
# Install dependencies
pip install requests python-dotenv

# Run tests
cd /Users/szablocsbeki/git/chaintelligence/api/tests
python3 test_api.py -v

# Run specific test
python3 test_api.py TestChaintelligenceAPI.test_06_price_by_cmc_id_single -v
```

## Test Coverage

### Public Endpoints (No Auth Required)

- `test_02_coin_list` - GET /api/coins/list
- `test_03_price_history` - GET /api/coin/price-history

### Coin Search Endpoints (No Auth Required)

- `test_29_coins_search_by_symbol` - GET /api/coins/search-by-symbol (coin info + contracts)
- `test_30_coins_search_by_symbol_lowercase` - Case-insensitive symbol search
- `test_31_coins_search_by_symbol_family_expansion` - include_coin_families=true (default) expands to family members
- `test_32_coins_search_by_symbol_no_family_expansion` - include_coin_families=false returns exact symbol only
- `test_33_coins_search_by_symbol_not_found` - Unknown symbol returns 404

### Origin & Destination Pairs (Auth Required)

- `test_23_ods_search_by_contract` - GET /api/ods/search-by-contract (default show_routes=true)
- `test_24_ods_search_by_contract_show_routes_false` - show_routes=false omits routes
- `test_25_ods_search_by_contract_no_match` - Unknown pair returns empty list
- `test_26_od_by_hash` - GET /api/od/{od_hash} returns the full O&D row
- `test_27_od_by_hash_not_found` - Unknown hash returns 404
- `test_28_od_by_hash_invalid` - Malformed hash returns 400

### Protected Endpoints (Auth Required)

- `test_03_date_range` - GET /api/routes/date-range
- `test_04_lp_summary` - GET /api/lp/position-summary
- `test_05_analyze_routes` - GET /api/routes/analyze

### Price by CMC ID Endpoint (Auth Required)

- `test_06_price_by_cmc_id_single` - Single ID query
- `test_07_price_by_cmc_id_multiple` - Multiple IDs query
- `test_08_price_by_cmc_id_invalid` - Invalid ID format (400 error)
- `test_09_price_by_cmc_id_too_many` - >100 IDs limit (400 error)
- `test_10_price_by_cmc_id_missing` - Missing parameter (422 error)

## Configuration

Tests use environment variables from `.env`:

- `API_URL` - Default: <http://localhost:8000>
- `PORTAL_USERNAME` - Default: admin
- `PORTAL_PASSWORD` - Default: chaintelligence

## Manual Testing

Test the price-by-cmc-id endpoint manually:

```bash
# From inside Docker container
docker exec chaintelligence-server bash -c \
  'curl -u admin:chaintelligence "http://localhost:8000/api/assets/price-by-cmc-id?id=1"'

# From local machine (if API exposed on localhost)
curl -u admin:chaintelligence 'http://localhost:8000/api/assets/price-by-cmc-id?id=1,1027'
```

## Expected Output

```text
Starting API Tests against http://localhost:8000...
test_01_coin_list ... ok
test_02_price_history ... ok
test_03_date_range ... ok
test_04_lp_summary ... ok
test_05_analyze_routes ... ok
test_06_price_by_cmc_id_single ... ok
test_07_price_by_cmc_id_multiple ... ok
test_08_price_by_cmc_id_invalid ... ok
test_09_price_by_cmc_id_too_many ... ok
test_10_price_by_cmc_id_missing ... ok

----------------------------------------------------------------------
Ran 10 tests in X.XXXs

OK
```

### Configure Environment

The tests use environment variables for authentication. You can either export them in your shell or create a `.env` file in the project root:

```bash
export PORTAL_USERNAME=admin
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
python api/tests/test_api.py
```

## Troubleshooting

- **401 Unauthorized**: Check your `PORTAL_USERNAME` and `PORTAL_PASSWORD` settings.
- **Connection Refused**: Ensure the server is running and `API_URL` is correct.
- **No data for selected date range**: This usually indicates the database is empty or the `swaps` table hasn't been synced for the test period.
