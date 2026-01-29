# Chaintelligence Portal

A unified DeFi analytics platform providing real-time LP portfolio tracking, transaction routing analysis, and Uniswap V3 historical backtesting.

## 🚀 Quick Start (Docker Deployment)

Deploy the entire stack (Postgres, Airflow, and the Portal Server) with a single command:

```bash
docker-compose up -d
```

Once running, access the services:

- **Main Portal**: [http://localhost:8000](http://localhost:8000)
- **Airflow UI**: [http://localhost:8081](http://localhost:8081)
- **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🛠 Prerequisites

- **Docker & Docker Compose**: Ensure you have Docker Desktop (Mac/Windows) or Docker Engine (Linux) with the Compose plugin installed.
- **Git**: To clone and manage the repository.

---

## 🔑 Configuration & Secrets

The application requires several environment variables to function. Create a `.env` file in the root directory based on the following requirements:

### Portal Authentication

- `PORTAL_USERNAME`: The admin username for the web portal (default: `admin`).
- `PORTAL_PASSWORD`: The admin password for the web portal.

### DeFi API Keys

- `GRAPH_API_KEY`: API key for [The Graph](https://thegraph.com/) (used for Uniswap V3 swap data).
- `ZAPPER_AUTH_HEADER`: Base64 encoded Basic Auth header for the [Zapper API](https://zapper.xyz/developers) (e.g., `Basic <encoded_key>`).
- `CRYPTOCOMPARE_API_KEY`: API key for [CryptoCompare](https://min-api.cryptocompare.com/) (used for historical token prices in the backtester).

### Monitoring Targets

- `TARGET_ADDRESS`: A comma-separated list of Ethereum/EVM addresses to track for LP positions (e.g., `0x123...,0x456...`).

### Airflow Internal Secrets (Required for DAG Scheduling)

These can be generated using standard Airflow security tools or kept consistent across a deployment:

- `INTERNAL_API_SECRET_KEY`
- `FERNET_KEY`
- `AIRFLOW_SECRET_KEY`
- `JWT_SECRET`

### Database

- `DATA_WAREHOUSE_DB`: Connection string for the internal Postgres database.
  - Default: `dbname=chaintelligence user=airflow password=airflow host=postgres port=5432`

---

## 📁 Project Structure

- `Dockerfile`: Multi-stage build for the Chaintelligence Portal.
- `docker-compose.yaml`: Orchestration for Airflow, Postgres, and the Portal.
- `routing/`: Core logic for fetching and analyzing swap routes.
- `routing-web/`: FastAPI backend and frontend for the portal.
- `lp-backtester/`: Historical Uniswap V3 strategy simulator.
- `chain-feeder/`: Airflow DAGs and ETL scripts for data ingestion.

---

## 🏗 Developer Notes

### Local Development (Non-Docker)

If you wish to run the portal server locally:

1. Install dependencies: `pip install -r routing/requirements.txt`
2. Run the server: `cd routing-web && python server.py`

### Testing LP Backtester

The backtester is available as a static mount at `/backtester` on the main portal. It handles strategy simulations locally in the browser.
