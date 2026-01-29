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

These keys are essential for Airflow 3.0 internal security and data encryption. **They must be identical across all Airflow services** (webserver, scheduler, dag-processor) in a single deployment.

- `FERNET_KEY`: Used to encrypt sensitive data (like API keys) in the Airflow metadata database.
- `INTERNAL_API_SECRET_KEY`: A shared secret that allows internal Airflow components to communicate securely.
- `JWT_SECRET`: Used to sign JSON Web Tokens for API authentication and session security.
- `AIRFLOW_SECRET_KEY`: Used by the web server for session management and CSRF protection.

> [!TIP]
> **Generating New Keys**: For a new deployment, you can generate a fresh key by running:
>
> ```bash
> docker run --rm apache/airflow:3.0.0 python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```
>
> You can reuse the same generated string for all four keys for simplicity, or generate unique ones for each.

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
