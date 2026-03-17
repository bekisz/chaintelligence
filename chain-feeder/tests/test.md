# Chain-Feeder Test Environment

This directory contains the test environment for the `chain-feeder` component, designed to run DAGs in an isolated Docker environment with a dedicated `chaintelligence_test` database.

## Prerequisites

- Docker
- Docker Compose

## Quick Start

Run the full test suite with **pytest**:

```bash
cd chain-feeder/tests
export PYTHONPATH=$PYTHONPATH:.
./venv/bin/pytest -v test_dags.py
```

Alternatively, you can use the legacy shell script:

```bash
cd chain-feeder/tests
./run_tests.sh
```

## Structure

- `docker-compose.test.yaml`: Isolated environment definition.
  - **Airflow**: <http://localhost:8085> (Port 8085 to avoid collision with main 8080)
  - **Postgres**: localhost:5435 (Port 5435 to avoid collision with main 5433)
  - **DB Name**: `chaintelligence`
- `verify_data.py`: Script to check data integrity in the test database.
- `run_tests.sh`: Automation script that executes two isolated phases:
  1. **Phase 1 (Family Updater)**: Starts env -> Triggers `coin_family_updater` -> Verifies `coin_family` table -> Tears down.
  2. **Phase 2 (Price Ingestion)**: Starts env -> Triggers `actual_coin_price_ingestion` -> Verifies price updates -> Tears down.
  - Optimized to skip full CMC mapping sync by injecting mock timestamps.
  - Robust error logging (captures scheduler/webserver logs on failure).

## Manual Testing

You can also manually interact with the environment:

1. **Start**: `docker compose -f docker-compose.test.yaml up -d`
2. **Access UI**: <http://localhost:8085>
3. **Connect DB**: `psql -h localhost -p 5435 -U airflow -d chaintelligence_test` (Password: airflow)
4. **Run Verification**: `docker exec -it airflow-scheduler-test python /opt/airflow/verify_data.py`
5. **Stop**: `docker compose -f docker-compose.test.yaml down`
