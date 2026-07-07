# PRD – Aerodrome (Base Chain) Support (Updated)

**Author:** szabi
**Date:** 2026‑07‑07
**Status:** Draft (needs stakeholder review)

---

## 1. Problem Statement

`chaintelligence` currently tracks LP positions and swap routes for Uniswap V3 (Ethereum, Arbitrum, Optimism, ...).
Aerodrome is a popular AMM built on **Base** (an OP‑compatible L2). Users cannot currently:

1. **View Aerodrome pool stats** (liquidity, APR, fees).
2. **Run route‑analysis** that includes Base‑based hops (e.g., ETH → Base → Aerodrome).
3. **Back‑test Base‑chain LPs** or see historical swap data for Aerodrome pools.

Because the platform’s data pipeline and routing engine are network‑agnostic (they rely on `dex_config.yaml` for factory addresses & init‑code hashes), adding Base/Aerodrome is a matter of **configuration + ETL support**.

### Why now?

* Base’s TVL has surpassed $10 B; Aerodrome is the #1 DEX on Base.
* Existing users have asked for cross‑chain arbitrage analysis that includes Base.
* The recent focus on “DB‑fetch performance” and “Arbitrum DEX config correctness” makes this a low‑risk addition.

---

## 2. Goals & Success Metrics

| Goal | Success Metric | Target |
|------|----------------|--------|
| **Data ingestion** – Pull Aerodrome swap events into the warehouse | Daily ETL run completes without errors; ≥ 95 % of Aerodrome swaps for the day are stored | 1 week after launch |
| **Routing engine** – Include Base in `POST /api/routes/analyze` | Routes that pass through Base are returned correctly (validated via unit‑test) | 2 weeks after launch |
| **UI visibility** – Show Aerodrome pools on the “Pools” page | UI lists Aerodrome pool cards; progress bar no longer stalls on Base data | 3 weeks after launch |
| **Back‑testing** – Enable Base‑chain LP back‑test | Users can select Base‑chain token pairs and receive back‑test results | 4 weeks after launch |
| **Operational reliability** – No regression in existing pipelines | All existing CI tests pass; performance of `analyze` endpoint stays ≤ 200 ms per chunk | Ongoing |

---

## 3. Scope

### In Scope

| Area | What will be done |
|------|-------------------|
| **Config** – Add Base network entries to `config/dex_config.yaml` (factory address, init‑code hash, token list). |
| **ETL** – Extend `chain‑feeder/include/uniswap_v4_range_fetcher.py` (or similar) to query Aerodrome subgraph (`https://api.thegraph.com/subgraphs/name/aerodrome/base`) and write to `swaps` tables. |
| **Routing** – `chain‑feeder/routing/postgres_fetcher.py` will now include Base in the `UNION ALL` of V3/V4 queries. |
| **Unit tests** – Add tests for Base‑chain data retrieval (`test_route_analyzer.py`, `test_shortcut_finder.py`). |
| **Frontend** – `web/static/app.js` and `routing.html` will accept Base as a selectable network; pool cards will show Aerodrome branding. |
| **Documentation** – Update `README.md`, `docs/architecture.md`, and create a “Base/Aerodrome Support” section. |
| **CI** – Add a new job to the Airflow DAG `base_aerodrome_sync.py` that runs nightly. |
| **Back‑fill** – On first deployment, run a three‑day back‑fill (t‑3 days → now) for Aerodrome swaps. |
| **Extended token list** – Include `WBTC` and `USDT` (and any other Base‑native tokens) in the `tokens` block. |
| **Config schema** – Allow multiple DEX factories per chain (list of `{name, factory_address, init_code_hash, tokens}`) to support future Base DEXes. |

### Out of Scope

* Full UI redesign – only minimal changes needed to expose Base networks.
* Support for other Base DEXes (e.g., BaseSwap) – focus on Aerodrome only.
* Production‑grade monitoring dashboards – will be added later.

---

## 4. User Stories

| ID | As a … | I want … | So that … |
|----|--------|----------|-----------|
| US‑001 | Analyst | to select **Base** as source/target chain in the route analysis UI | I can see cross‑chain paths that include Aerodrome |
| US‑002 | LP holder | to view **Aerodrome** pool statistics (liquidity, APR, fees) | I can assess the health of my Base‑chain positions |
| US‑003 | Power‑user | to back‑test a **Base‑chain LP** strategy | I can compare historic returns against other chains |
| US‑004 | Engineer | for the ETL to automatically ingest Aerodrome swaps nightly | I don’t have to run ad‑hoc scripts |
| US‑005 | QA | a test that validates Base‑chain data appears in the `analyze` endpoint | My CI pipeline catches regressions early |
| US‑006 | Engineer | to back‑fill the last three days of Aerodrome data on first run | Historical charts start with recent data |
| US‑007 | Engineer | to add additional Base native tokens (WBTC, USDT) to the token registry | Full token coverage for Base users |
| US‑008 | Architect | a config schema that can hold several DEX factories per chain | Future‑proofing for other Base DEXes |

---

## 5. Functional Requirements

| FR# | Description |
|-----|-------------|
| **FR‑001** | **Network definition** – `config/dex_config.yaml` must contain a `base` block that can hold **multiple** DEX definitions (list). Each entry includes `name`, `factory_address`, `init_code_hash`, and a `tokens` list. |
| **FR‑002** | **Subgraph integration** – Add a new GraphQL endpoint (`https://api.thegraph.com/subgraphs/name/aerodrome/base`) to `uniswap_v4_range_fetcher.py` with a query that returns swaps (timestamp, tx_hash, log_index, tokenIn, tokenOut, amountIn, amountOut, feeBps). |
| **FR‑003** | **Database schema** – No new columns needed; existing `swaps` table can store Base‑chain swaps. Ensure `chain_id` column is set to the Base chain ID (8453). |
| **FR‑004** | **Routing query** – `postgres_fetcher.py` must include Base in the `UNION ALL` clause and filter by `chain_id = 8453` where appropriate. |
| **FR‑005** | **API exposure** – The `POST /api/routes/analyze` endpoint must accept `network=base` (or `chain=8453`) and return results in the same NDJSON streaming format. |
| **FR‑006** | **Frontend network selector** – Add “Base” to the chain dropdown in `routing.html` and ensure that when selected, Aerodrome pools are displayed in `app.js`. |
| **FR‑007** | **Pool card UI** – Show Aerodrome logo (use placeholder SVG) and label “Aerodrome (Base)”. |
| **FR‑008** | **Back‑test CLI** – `python test_backtest.py --network base` should invoke the existing back‑test runner with Base‑specific token addresses. |
| **FR‑009** | **CI tests** – Add at least two new pytest cases covering: (a) successful fetch of a sample Aerodrome swap; (b) route analysis that includes a Base hop. |
| **FR‑010** | **Documentation** – Add a “Base Chain (Aerodrome)” section to `docs/architecture.md` covering data flow and config values. |
| **FR‑011** | **Three‑day back‑fill** – On first deployment, run a one‑off DAG that pulls Aerodrome swaps for the previous three days (`now - 3d` → `now`). |
| **FR‑012** | **Extended token list** – Include `WBTC`, `USDT` (plus any other Base‑native tokens) in the `tokens` block of the Aerodrome factory entry. |
| **FR‑013** | **Config schema extension** – Change the `base` config from a single map to a list called `dexes`:
```yaml
base:
  dexes:
    - name: "Aerodrome"
      factory_address: "0x..."
      init_code_hash: "0x..."
      tokens: [...]
    - name: "AnotherDEX"
      factory_address: "0x..."
      init_code_hash: "0x..."
      tokens: [...]
```
All downstream code must iterate over `config.base.dexes` instead of a single object. |

---

## 6. Non‑Functional Requirements

| NFR# | Requirement |
|------|-------------|
| **NFR‑001** | **Performance** – Adding Base should not increase per‑chunk latency of the `/api/routes/analyze` endpoint by > 30 ms. |
| **NFR‑002** | **Reliability** – ETL job must have retry logic (max 3 attempts, exponential back‑off). |
| **NFR‑003** | **Scalability** – Swaps table will grow ~5 M rows/month for Base; indexes on `chain_id` and `block_timestamp` must be present. |
| **NFR‑004** | **Security** – No new secrets required; the GraphQL endpoint is public. |
| **NFR‑005** | **Observability** – Emit a log line “Base‑Aerodrome sync completed: X rows” at INFO level. |
| **NFR‑006** | **Compatibility** – Must work with existing PostgreSQL version (13) and continue to compile on Python 3.11. |

---

## 7. Technical Design

### 7.1 Configuration
```yaml
# config/dex_config.yaml (excerpt)
base:
  dexes:
    - name: "Aerodrome"
      factory_address: "0x... (Aerodrome factory address on Base)"
      init_code_hash: "0x... (init code hash for Aerodrome pools)"
      tokens:
        - name: USDC
          address: "0x..."
        - name: WETH
          address: "0x..."
        - name: AERO
          address: "0x..."
        - name: USDT
          address: "0x..."
        - name: WBTC
          address: "0x..."
    # Future Base DEXes can be added here
```
*The existing `ethereum`, `arbitrum`, `optimism` blocks remain unchanged.*

### 7.2 ETL Changes
1. **New DAG** – `chain-feeder/dags/base_aerodrome_sync.py` (inherits from existing The Graph DAG). It runs nightly and also includes a **one‑off back‑fill** for the past three days when the DAG is first triggered.
2. **Fetcher** – Extend `uniswap_v4_range_fetcher.py` with a function `fetch_aerodrome_swaps(start_ts, end_ts)` that uses the Aerodrome subgraph.
3. **Insert** – Use existing `insert_swaps()` helper to write rows, passing `chain_id=8453`.

### 7.3 Routing Query
```sql
-- In postgres_fetcher.py, part of the UNION ALL
SELECT *
FROM swaps
WHERE chain_id = 8453   -- Base
  AND block_timestamp BETWEEN $1 AND $2
```
Add the same `UNION ALL` logic already present for other chains.

### 7.4 API Layer
`api/main.py` already forwards all routes to `chain-feeder/routing`. No code changes required; the new `chain_id` will be automatically included when the request payload includes `network: "base"` (converted to `8453`).

### 7.5 Frontend
* `web/static/routing.html` – Add `<option value="base">Base</option>` to network selector.
* `web/static/app.js` – Extend `NETWORKS` constant with `{id: 8453, name: "Base", logo: "aerodrome.svg"}`.
* When drawing pool cards, if `pool.dex === "Aerodrome"` show the Aerodrome logo.

### 7.6 Tests
```bash
cd chain-feeder/routing
python -m pytest test_route_analyzer.py::test_base_route
python -m pytest test_shortcut_finder.py::test_aerodrome_pool_detection
```
Create mock swap data for Base (use fixtures). Add a test that verifies the three‑day back‑fill writes the expected number of rows.

### 7.7 CI/CD
* Add the new DAG to the Airflow `docker-compose.yaml` environment variables.
* Update the CI workflow (`.github/workflows/ci.yml`) to run the new pytest files.
* Ensure the back‑fill runs once on a clean environment (e.g., via a `run_once` flag).

---

## 8. Dependencies

| Dependency | Reason | Owner |
|------------|--------|-------|
| **Aerodrome subgraph URL** | Required for swap data | Engineering |
| **Base chain ID (8453)** | Needed in DB & queries | Engineering |
| **Aerodrome factory address & init‑code hash** | Config file entry | Product / Architecture |
| **Docker images** | Must rebuild `chaintelligence-server` with updated config | DevOps |
| **CI runner with Python 3.11** | For new test modules | CI Engineer |

---

## 9. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Subgraph downtime → ETL failures | Data gaps | Medium | Retry with exponential back‑off; fallback to raw RPC fetch (optional). |
| Incorrect factory address → wrong pool IDs | Users see missing pools | Low | Verify address against Aerodrome docs before merge; add unit‑test that asserts a known pool ID resolves. |
| Increased DB size → slower queries | Performance degradation | Low | Add index on `chain_id`; monitor query plans. |
| UI regressions on older browsers | Broken UI | Low | Run existing UI test suite on Chrome/Firefox; use feature‑detect for new logo asset. |
| Config schema change breaking existing code | Runtime errors | Low | Update all config‑loading code to iterate over `dexes` list; add backward‑compatible fallback if only a single map is present. |

---

## 10. Timeline (Assuming 2‑week sprints)

| Sprint | Deliverable |
|--------|-------------|
| **Sprint 1** (Week 1) | Add Base config (multi‑dex schema), implement fetcher, create DAG skeleton, implement three‑day back‑fill logic. |
| **Sprint 2** (Week 2) | Extend DB ingestion, write unit tests, CI integration, add extended token list (WBTC, USDT). |
| **Sprint 3** (Week 3) | UI changes (network selector, pool cards), manual QA. |
| **Sprint 4** (Week 4) | Full end‑to‑end testing, performance benchmarking, documentation. |
| **Sprint 5** (Week 5) | Release to staging, monitor ETL runs, prepare production rollout. |

---

## 11. Acceptance Criteria

- [ ] `config/dex_config.yaml` contains a `base` block with a `dexes` list, including the Aerodrome entry and the extra tokens `WBTC`, `USDT`.
- [ ] Nightly Airflow DAG populates `swaps` with ≥ 95 % of Aerodrome events for the previous day.
- [ ] A one‑off three‑day back‑fill runs on first deployment and logs the row count.
- [ ] `POST /api/routes/analyze` with `network=base` returns a valid NDJSON stream within the baseline latency budget.
- [ ] The UI shows “Base” in the network dropdown and displays Aerodrome pool cards with correct token symbols and APRs.
- [ ] All existing test suites pass (`100 %`); new Base tests (including back‑fill) pass.
- [ ] Documentation updated and reviewed by the product team.

---

## 12. Open Questions (Resolved)

1. **Three‑day back‑fill** – Implemented as a one‑off run of the new DAG on first launch.
2. **Additional Base tokens** – `WBTC` and `USDT` added to the token list.
3. **Multiple DEX factories** – Config schema now supports a `dexes` list per chain, future‑proofing for other Base DEXes.

---

*End of Updated PRD.*