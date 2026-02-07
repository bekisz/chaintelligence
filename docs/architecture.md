# System Architecture: Chaintelligence

Chaintelligence is a comprehensive DeFi analytics platform designed to provide real-time portfolio tracking, route analysis for token swaps, and historical backtesting for Uniswap V3 liquidity positions.

## 📁 Repository Structure

The codebase is organized into distinct layers that mirror the architectural tiers:

```text
chaintelligence/
├── api/                          # Layer 3: Application Server (FastAPI)
│   ├── main.py                   # API routes, authentication, business logic
│   ├── requirements.txt          # Python dependencies
│   └── tests/                    # API integration tests
│       ├── test_api.py           # Automated endpoint validation
│       └── README.md             # Testing documentation
│
├── web/                          # Layer 4: Presentation Layer
│   ├── static/                   # Main portal UI assets
│   │   ├── index.html            # Landing page
│   │   ├── routing.html          # Route Analysis Terminal
│   │   ├── lp.html               # Portfolio Dashboard
│   │   ├── app.js                # Route analysis logic
│   │   ├── lp.js                 # Portfolio rendering
│   │   ├── style.css             # Unified styling
│   │   └── nav.js                # Navigation component
│   └── backtest/                 # LP Backtester (standalone app)
│       ├── index.html            # Backtester UI
│       ├── logic.js              # Strategy simulation engine
│       └── docs/                 # Strategy documentation
│
├── chain-feeder/                 # Layer 1: ETL & Ingestion (Airflow)
│   ├── dags/                     # Airflow DAG definitions
│   │   ├── cmc_coin_map_sync.py
│   │   ├── zapper_balance_loader_dag.py
│   │   ├── the_graph_uniswap_v3_swaps_dag.py
│   │   └── uniswap_v3_history_sync.py
│   ├── routing/                  # Shared business logic
│   │   ├── postgres_fetcher.py   # Database abstraction
│   │   ├── route_analyzer.py     # Graph-walking algorithms
│   │   └── config.py             # Connection strings
│   └── include/
│       ├── sql/init_db.sql       # Schema definitions
│       └── scripts/              # Utility scripts
│
├── docs/                         # Documentation
│   └── architecture.md           # This file
│
├── docker-compose.yaml           # Container orchestration
├── Dockerfile                    # API server image
└── .env                          # Environment configuration
```

### Key Organizational Principles

1. **API Layer (`api/`)**: Contains all server-side logic, authentication, and database interaction. This is the exclusive gateway for frontend requests.

2. **Web Layer (`web/`)**: Strictly presentation-focused. All components communicate exclusively with the API layer via HTTP. No direct database or external API access.

3. **Chain Feeder (`chain-feeder/`)**: Autonomous data ingestion pipelines. Operates independently from the web application, ensuring data freshness without blocking user interactions.

4. **Shared Logic (`chain-feeder/routing/`)**: Business logic modules (like `RouteAnalyzer`) are imported by both Airflow DAGs and the FastAPI server, ensuring consistency between batch processing and real-time queries.

## 📊 System Architecture Diagram

```mermaid
graph TD
    subgraph External_Data_Sources [Layer 0: External APIs]
        CMC[CoinMarketCap API]
        CC[CryptoCompare API]
        TG[The Graph - Uniswap Subgraphs]
        ZP[Zapper API]
    end

    subgraph Ingestion_Layer [Layer 1: ETL & Ingestion - Airflow]
        DAG_CMC[CMC Sync DAG]
        DAG_Price[Price Update DAG]
        DAG_Swap[Swap Sync DAG]
        DAG_Zapper[Zapper Portfolio DAG]
        DAG_Agg[History Aggregation DAG]
    end

    subgraph Storage_Layer [Layer 2: Unified Data Warehouse - PostgreSQL]
        T_Coin[(Coin Metadata)]
        T_Family[(Coin Families)]
        T_Price[(Price History)]
        T_Swaps[(Swap Records)]
        T_Pools[(Liquidity Pools)]
        T_Stats[(Pool Performance Stats)]
    end

    subgraph Logic_Layer [Layer 3: Application Server - FastAPI]
        API_Route[Route Analyzer]
        API_Yield[Yield & APR Engine]
        API_Meta[Metadata Resolver]
    end

    subgraph Presentation_Layer [Layer 4: Interactive Frontend]
        UI_Dash[Portfolio Dashboard]
        UI_Route[Routing Terminal]
        UI_Back[LP Backtester]
    end

    %% Data Flow & Dependencies
    CMC --> DAG_CMC
    CC --> DAG_Price
    TG --> DAG_Swap
    ZP --> DAG_Zapper

    DAG_CMC --> T_Coin
    DAG_CMC --> T_Family
    DAG_Price --> T_Price
    DAG_Swap --> T_Swaps
    DAG_Zapper --> T_Pools
    T_Swaps --> DAG_Agg
    DAG_Agg --> T_Stats

    T_Coin & T_Price & T_Swaps & T_Stats --> Logic_Layer
    
    %% Presentation Layer ONLY reaches Logic Layer
    Logic_Layer --> Presentation_Layer
```

## 🏗️ High-Level Component Overview

The system follows a strict **N-Tier Architecture**, where the Presentation Layer is fully decoupled from data storage and external providers. All client requests are mediated by the Logic Layer (FastAPI), ensuring centralized authentication, rate limiting, and data normalization.

### 1. Unified Data Warehouse (PostgreSQL)

The central source of truth for all indexed blockchain and off-chain data.

- **Relational Model**: Optimized for cross-referencing on-chain positions with market metadata.
- **Key Tables**:
  - `coin`: Metadata for tracked cryptocurrencies (CMC rank, contract addresses, etc.).
  - `coin_family`: Many-to-many mapping for grouped assets (e.g., USD family, BTC family).
  - `liquidity_pool`: Registry of standardized Uniswap V3 and DeFi pools.
  - `liquidity_pool_position`: Current active LP positions across tracked wallets.
  - `uniswap_v3_swaps`: Historical transactional data used for volume and yield analysis.
  - `coin_price_history`: Multi-year time-series price data.

**See the [Detailed Database Schema](../chain-feeder/docs/SCHEMA.md) for table definitions and relational constraints.**

### 2. ETL & Ingestion Layer (Apache Airflow)

Located in `chain-feeder/dags/`, automated pipelines (DAGs) are responsible for keeping the Data Warehouse in sync with the physical world.

- **cmc_coin_map_sync**: Discovers new tokens and updates market rankings from CoinMarketCap.
- **zapper_lp_ingestion**: Regularly fetches active portfolio data from Zapper for a set of target addresses.
- **the_graph_uniswap_v3_swaps**: Successive indexing of on-chain swap events from The Graph.
- **coin_price_update**: High-frequency price updates and a graduated historical backfill system:
  - **Top 100 Coins**: Full historical depth.
  - **Coins 100-1000**: Rolling 2-year window.
- **uniswap_v3_history_sync**: Periodically aggregates millions of swap records into daily pool statistics (volumes, APRs).

### 3. Application Server (FastAPI) - "The Logic Layer"

Located in `api/main.py`, this is the exclusive gateway for all frontend interaction.

- **Route Analyzer**: Implements complex graph-walking logic to find optimal swap paths using historical execution data.
- **Yield Engine**: Calculates APRs based on realized fee accumulation vs. TVL.
- **Metadata Resolver**: Normalizes user input (e.g., resolving a family like "USD" into its components like USDC/USDT/DAI).
- **Security & Proxying**: Mediates access to internal data and proxies external metadata (like coin rankings) to avoid direct client-side external dependencies.
- **Authentication**: HTTP Basic Auth middleware protects sensitive endpoints while allowing public access to metadata APIs.

**API Endpoints:**

- `/api/routes/analyze` - Route analysis with APR enrichment
- `/api/routes/date-range` - Available swap data timeframe
- `/api/lp/position-summary` - Aggregated LP portfolio snapshots
- `/api/coin/list` - Token metadata (public)
- `/api/coin/price-history` - Historical price data (public)

### 4. Interactive Frontend - "The Presentation Layer"

Located in `web/`, a modern web-based interface for data visualization. **Strictly limited to API communication.**

- **Route Analysis Terminal** (`web/static/routing.html`): Visualizes swap paths, market sizes, execution counts, and APR metrics with interactive tooltips.
- **Portfolio Dashboard** (`web/static/lp.html`): Displays active LP positions with real-time range monitoring, fee accrual tracking, and multi-wallet filtering.
- **LP Backtester** (`web/backtest/`): A standalone simulator for testing Uniswap V3 strategies against historical price volatility with multiple rebalancing strategies.

**Frontend Architecture:**

- Pure HTML/CSS/JavaScript (no build step required)
- Modular component design with shared navigation (`nav.js`)
- Version-controlled cache busting for CSS/JS assets
- Responsive design with glassmorphism UI patterns

---

## 🛰️ Integration Points

Chaintelligence integrates with several key infrastructure providers via the **Ingestion Layer** (for bulk data) or **Logic Layer** (for just-in-time metadata):

- **CoinMarketCap**: Source for authoritative token discovery, rankings, and Ethereum contract addresses.
- **CryptoCompare**: Provides sub-minute price data and long-form historical OHLCV data.
- **The Graph**: Used for querying distributed ledger events (Uniswap V3 subgraphs).
- **Zapper**: Leveraged for cross-protocol portfolio tracking and position identification.

---

## ⚡ Reliability & Performance Patterns

- **API Batching**: Outbound requests to providers (like CryptoCompare) are automatically batched (e.g., 50 symbols per batch) to maximize throughput and avoid URL limit constraints.
- **Database Triggers**: Automatic normalization (e.g., forcing uppercase symbols) via PL/pgSQL triggers ensures data consistency regardless of the ingestion source.
- **Asset-Based Scheduling**: Airflow tasks are linked via Assets/Datasets to ensure downstream daily history aggregations only run when the raw swap data is ready.
- **Micro-Batch Processing**: Large-scale swap analysis is chunked by time-windows to maintain responsive API performance under load.
- **Presentation Decoupling**: Frontend components never maintain direct connections to PostgreSQL or external data providers, ensuring a secure and manageable "Logic Gateway" pattern.
- **Stateless API Design**: The FastAPI server is fully stateless, enabling horizontal scaling and simplified container orchestration.

---

## 🔒 Security Architecture

- **Authentication Middleware**: HTTP Basic Auth protects all sensitive endpoints while allowing public access to metadata APIs.
- **Environment-Based Secrets**: All API keys and credentials are managed via environment variables, never committed to version control.
- **CORS & Rate Limiting**: Configured at the FastAPI layer to prevent abuse and unauthorized access.
- **Docker Network Isolation**: Services communicate via internal Docker networks, with only necessary ports exposed to the host.
