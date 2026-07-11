# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Chaintelligence is a DeFi analytics platform: real-time LP portfolio tracking, swap-route analysis, and Uniswap V3 backtesting. It is an N-tier system — Airflow ETL → PostgreSQL warehouse → FastAPI logic layer → static HTML/JS frontend. The frontend **never** touches Postgres or external APIs directly; it only talks to the FastAPI layer.

Note: `README.md` and `docs/architecture.md` reference some legacy directory names (`routing/`, `routing-web/`, `lp-backtester/`). The real layout is `api/`, `web/`, `chain-feeder/` as described below.

## Architecture

- **`api/main.py`** — the entire FastAPI application (single ~65KB file). All routes, auth middleware, business logic, and Airflow proxying live here. Run with `python api/main.py` (uvicorn on `:8000`). Imports routing logic from `chain-feeder/routing/` by inserting it onto `sys.path`, and loads DEX factory/init-hash config from `config/dex_config.yaml`.
- **`chain-feeder/`** — Airflow ETL layer. `dags/` are the ingestion pipelines (CMC, CryptoCompare, The Graph V3/V4 swaps, RPC claim/event backfills, daily history aggregation). `include/` holds API clients (`coinmarketcap_client.py`, `cryptocompare_client.py`, `graph_discovery_client.py`, `rpc_discovery_engine.py`, `uniswap_v*_range_fetcher.py`) and `sql/init_db.sql` (the warehouse schema, applied on first Postgres boot). `routing/` is shared business logic imported by **both** the DAGs and the API server.
- **`chain-feeder/routing/`** — the shared logic core. `postgres_fetcher.py` (swap-data queries, including V3+V4 `UNION ALL` branches), `route_analyzer.py` (reconstructs multi-hop routes by grouping swaps by tx hash and ordering by log index), `shortcut_finder.py`, and `config.py` (loads token registry from the `coin` table at import time; falls back to a static USDC/USDT/WETH/WBTC set if the DB is unreachable).
- **`web/`** — pure presentation, no build step. `web/static/` is the main portal (`routing.html`+`app.js`, `lp.html`+`lp.js`, `pool.*`, `sps.*`, `api.html`, shared `nav.js`/`style.css`). `web/backtest/` is the standalone LP backtester mounted at `/backtester`.
- **`config/dex_config.yaml`** — per-network factory addresses + init code hashes for V3-style DEXes (Uniswap V3, PancakeSwap V3). `get_factory_and_hash(protocol, network)` in `main.py` reads this; pool contract addresses are derived via CREATE2 and cached in module-level `POOL_ADDRESS_CACHE`/`FACTORY_HASH_CACHE`.

### Request flow: `/api/routes/analyze`

The flagship endpoint streams NDJSON. It chunks the date range, runs each chunk's DB fetch in a worker thread via `asyncio.to_thread` (so the event loop stays responsive — this was a recent fix for the UI sticking at 0%), emits `{"type":"progress","pct":...}` lines, then builds the route graph, enriches with pool stats/APRs (also threaded), derives pool addresses, and finally emits one `{"type":"result","data":...}`. The frontend (`app.js`) reads the stream and updates the progress bar. When touching this endpoint, preserve the streaming + thread-offload pattern.

### Auth model

HTTP Basic Auth middleware in `main.py`. Sensitive routes require `PORTAL_USERNAME`/`PORTAL_PASSWORD`. A hardcoded `exempt_paths` list bypasses auth (`/api/coin/list`, `/api/coin/price-history`, `/backtester`, `/pool`, `/static`, `/api/sps`, `/sps`, `/api/lp`, ...). When adding a public endpoint, add its path prefix to `exempt_paths` or it will silently require auth.

## Commands

```bash
# Full stack (Postgres + Airflow + Portal)
docker-compose up -d
# Portal:      http://localhost:8000      (docs at /docs)
# Airflow UI:  http://localhost:8081      (airflow/airflow)

# Just the API server
docker-compose up chaintelligence-server

# Local dev (non-Docker) — needs .env loaded for DATA_WAREHOUSE_DB etc.
pip install -r api/requirements.txt   # + fastapi uvicorn psycopg2-binary python-dotenv
python api/main.py

# API tests (server must be running; uses PORTAL_USERNAME/PORTAL_PASSWORD + API_URL)
docker exec chaintelligence-server python api/tests/test_api.py -v
docker exec chaintelligence-server python api/tests/test_api.py TestChaintelligenceAPI.test_06_price_by_cmc_id_single -v   # single test

# Routing-logic unit tests (no server needed)
cd chain-feeder/routing && python test_route_analyzer.py && python test_shortcut_finder.py
```

Postgres is exposed on host port **5433** (container 5432) so local dev can connect to the same DB the Airflow containers write to. The default `DATA_WAREHOUSE_DB` in `chain-feeder/routing/config.py` targets `host=localhost port=5433`.

## Configuration & secrets

Two env files, both loaded via `env_file` in `docker-compose.yaml` and by `load_dotenv`:

- **`.env.config`** — public, tracked in git (CMC tier tuning, `RPC_DISCOVERY_START_DATE`, `SKIP_CLAIM_NETWORKS`, etc.).
- **`.env.secrets`** — gitignored secrets. Copy from `.env.secrets.example`. Holds `GRAPH_API_KEY`, `CMC_API_KEY`, `CRYPTOCOMPARE_API_KEY`, `RPC_URL`, `DATA_WAREHOUSE_DB`, Airflow security keys, and `PORTAL_USERNAME`/`PORTAL_PASSWORD`.

`.env` (a directory here) is also mounted into the container at `/app/.env`; `main.py` calls `load_dotenv(ROOT_DIR/.env)`.

## Conventions worth knowing

- **Create CLI/testable solutions first, not UI-first** (`.agent/rules/implemantion-guide.md`, `trigger: always_on`). Prefer a script you can run from the shell over a frontend change when prototyping.

- Schema lives in `chain-feeder/include/sql/init_db.sql`; incremental migrations are sibling `.sql` files (e.g. `add_pool_address.sql`, `create_position_events_table.sql`, `update_lp_view_price_calculation.sql`). `chain-feeder/docs/SCHEMA.md` documents the tables; `chain-feeder/docs/DAGS.md` documents the DAGs.
- The repo root accumulates many throwaway scripts (`test_*.py`, `fix_*.py`, `debug_*.py`, `scratch/`). These are scratch/debugging artifacts, not part of the application — don't treat them as the source of truth and feel free to ignore them when reasoning about structure.

## Recent focus areas (from git history)

DB-fetch performance and UI progress reporting on the routing endpoint, and Arbitrum DEX config correctness (factory addresses, init code hashes, token decimals). When changing DEX/network support, update `config/dex_config.yaml` and verify the per-network factory/init-hash there rather than hardcoding.
