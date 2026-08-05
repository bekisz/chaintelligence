# Chaintelligence Portal

A unified DeFi analytics platform providing real-time LP portfolio tracking, transaction routing analysis, and Uniswap V3 historical backtesting.

[**Read the Detailed Architecture Documentation**](docs/architecture.md)

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

The application uses a dual-environment file structure to separate public configuration from private secrets:

1. **`.env.config`**: Contains public application settings (intervals, target addresses, database names). **This file is tracked in Git.**
2. **`.env.secrets`**: Contains sensitive API keys and passwords. **This file is ignored by Git.**

### Setup Instructions

1. **Copy the template**:

   ```bash
   cp .env.secrets.example .env.secrets
   ```

2. **Fill in the secrets** in `.env.secrets` using the sources listed below.
3. (Optional) **Modify public config** in `.env.config`

---

### Where to get the Keys

| Secret | Provider | Purpose |
| :--- | :--- | :--- |
| `GRAPH_API_KEY` | [The Graph Studio](https://thegraph.com/studio/) | Uniswap V3/V4 on-chain event data |
| `CMC_API_KEY` | [CoinMarketCap API](https://coinmarketcap.com/api/) | Real-time prices & metadata |
| `RPC_URL` | [Ankr](https://www.ankr.com/) / [Alchemy](https://www.alchemy.com/) | Direct Ethereum node connectivity |

### Airflow Security Keys

The following keys are required for internal Airflow 3.0 security. You can generate them by running:

```bash
# Generate a single key suitable for all 4 slots
openssl rand -base64 32
```

- `INTERNAL_API_SECRET_KEY`, `FERNET_KEY`, `AIRFLOW_SECRET_KEY`, `JWT_SECRET`

---

### Essential Public Config (`.env.config`)

- `TARGET_ADDRESS`: Comma-separated list of EVM addresses to monitor.
- `DATA_WAREHOUSE_DB`: Connection string for the internal database.
- `PORTAL_USERNAME`: Admin login for the portal dashboard.

---

## 📁 Project Structure

- `Dockerfile`: Multi-stage build for the Chaintelligence Portal server.
- `docker-compose.yaml`: Orchestration for Airflow, Postgres, and the Portal.
- `api/`: FastAPI application server — `main.py` (all routes, auth, business logic) plus `api/routing/` (analysis core: route analyzer, shortcut finder, undercut simulator).
- `web/`: Frontend — `web/static/` (main portal) and `web/backtest/` (standalone LP backtester mounted at `/backtester`).
- `chain-feeder/`: Airflow DAGs and ETL scripts for data ingestion (ETL-only; no business logic).
- `config/`: Per-network YAML (DEX factory/init-hash, chains, coin families, swap retention).
- `docs/`: Architecture, feature notes, and concept docs.

---

## 🏗 Developer Notes

### Local Development (Non-Docker)

If you wish to run the portal server locally (requires `.env` loaded for `DATA_WAREHOUSE_DB` etc.):

```bash
pip install -r api/requirements.txt
python api/main.py
```

### Testing LP Backtester

The backtester is available as a static mount at `/backtester` on the main portal. It handles strategy simulations locally in the browser.
