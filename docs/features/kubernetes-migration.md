# PRD: Kubernetes Migration

## 1. Problem Statement

Chaintelligence is currently operated via a single `docker-compose.yaml` that
brings up Postgres, Airflow (webserver/scheduler/dag-processor/init), and the
FastAPI portal together. This setup has served the project well for local
development and a single-host deployment, but it now constrains the project in
several ways:

1. **Single-host bottleneck.** Every component (stateful Postgres, Airflow
   scheduler, FastAPI server) runs on one machine. There is no horizontal
   scaling of the API under concurrent routing-analysis load, no scheduler HA,
   and no way to grow storage or compute independently per component.
2. **Dev/prod parity gap.** The compose file is simultaneously a dev environment
   *and* the production deployment. Bind-mounted source directories
   (`./api`, `./web`, `./chain-feeder`, `./config`) mean production runs from a
   checked-out working tree rather than versioned images — drift between hosts
   is silent and rollbacks are manual.
3. **No environment separation.** Secrets (`.env.secrets`) and public config
   (`.env.config`) are flat files loaded the same way everywhere. There is no
   staging, no isolated secrets backend, and no per-environment overlay.
4. **Stateful data has no resilience story.** The `postgres-db-volume` Docker
   volume is a single copy on one host. There is no PITR, no WAL archiving, no
   automated backups, no failover — the warehouse (20M+ swap rows) and the
   Airflow metadata DB are both at risk.
5. **Operational primitives are missing.** No health-based autoscaling, no
   centralized logs/metrics, no Ingress/TLS termination, no rolling updates.
   Each restart of `airflow-webserver` or `chaintelligence-server` is a
   `docker-compose restart` with downtime.
6. **Airflow runs `LocalExecutor`.** The scheduler process is the only
   concurrency boundary; DAGs that do heavy RPC backfills (claim scanning,
   Zapper LP ingestion, V3/V4 history sync) cannot fan out across workers.

### Scope

This PRD covers migrating the **entire stack** — Postgres, Airflow, FastAPI
portal, and static frontend — from docker-compose to Kubernetes, with two
target environments:

- **Dev:** a local `k3d`/`kind` cluster that replaces `docker-compose up`.
- **Prod:** a managed Kubernetes cluster (EKS / GKE / AKS) with HA and
  operational hardening.

Stateful services run **in-cluster** (CloudNativePG for Postgres, the official
Airflow Helm chart with an in-cluster metadata DB) in both environments, with
the option to externalize Postgres later documented as an open question.

---

## 2. Goals & Non-Goals

### Goals

- **G1 — One Helm chart, two environments.** A single `deploy/` Helm tree with
  `values-dev.yaml` and `values-prod.yaml` overlays deploys the full stack to
  a local k3d/kind cluster and a managed-cloud prod cluster from the same
  templates.
- **G2 — In-cluster stateful services.** Postgres runs as a CloudNativePG
  `Cluster` (dev: 1 replica, local-path; prod: 2–3 replicas, cloud disk, WAL
  archiving + scheduled backups). Airflow runs via the official Helm chart
  against the in-cluster PG.
- **G3 — No host bind-mounts in prod.** All workloads run from versioned
  container images built in CI. DAGs are delivered via `gitSync` sidecar so
  scheduler/dag-processor pick up DAG changes without an image rebuild.
- **G4 — Secrets and config split.** `.env.config` → ConfigMap; `config/`
  (`dex_config.yaml`, `chains.yaml`) → ConfigMap; `.env.secrets` →
  ExternalSecret Operator pulling from the cloud secrets manager (AWS Secrets
  Manager / GCP Secret Manager / Azure Key Vault). Dev uses plain `Secret`
  resources for parity without the cloud dependency.
- **G5 — Production HA and autoscaling.** API and Airflow webserver run ≥2
  replicas with HPA on CPU/RPS; scheduler runs ≥2 with
  `leaderLock`/`scheduler_heartbeat`; dag-processor runs ≥2.
- **G6 — TLS and ingress.** A single `Ingress` (nginx) with cert-manager-issued
  certificates fronts the API (`/`) and Airflow UI (`/airflow`). Postgres is
  in-cluster only — no host port.
- **G7 — Observability.** Structured logs to stdout (collected by the cluster),
  Prometheus metrics for the API and Airflow, basic dashboards and alerts.
- **G8 — Dev/prod parity.** `helm upgrade --install chaintelligence ./deploy
  -f values-dev.yaml` on k3d produces a topology equivalent to prod, only
  smaller.

### Non-Goals

- Rewriting application code to be "cloud-native." The FastAPI app, DAGs, and
  `routing/` logic stay as-is; only packaging and deployment change.
- Migrating off Postgres or changing the warehouse schema. (`init_db.sql` and
  sibling migrations remain the source of truth and are applied by CNPG
  bootstrap / init jobs.)
- Adopting CeleryExecutor for Airflow in the first cut (documented as an open
  question — dev/prod both start on `LocalExecutor`, with the chart ready to
  flip to Celery + Redis).
- Multi-region deployment, DR replication, or service mesh.
- Externalizing Postgres to a managed DB (RDS/Cloud SQL) — kept as an explicit
  open question for a later phase.

---

## 3. Current State Analysis

### 3.1 What `docker-compose.yaml` does today

| Service | Image / build | Role | Volumes / ports |
|---|---|---|---|
| `postgres` | `postgres:17` | Warehouse + Airflow metadata (single DB `airflow`, user `airflow`); runs `init_db.sql` on first boot | `postgres-db-volume` (Docker volume), host port `5433:5432` |
| `airflow-init` | `apache/airflow:3.1.7` (+ `Dockerfile.airflow`) | DB migrate + admin user create, runs once | shared `airflow-common` volumes |
| `airflow-webserver` | same | `api-server` command, Airflow UI + REST API | host port `8081:8080` |
| `airflow-scheduler` | same | `scheduler` command | — |
| `airflow-dag-processor` | same | `dag-processor` command | — |
| `chaintelligence-server` | `Dockerfile` (python:3.13-slim) | FastAPI on `:8000`, imports `chain-feeder/routing/` and `config/dex_config.yaml` | bind-mounts `api/`, `web/`, `chain-feeder/`, `config/`, `.env`; host port `8000:8000` |

**Airflow config highlights:** `LocalExecutor`, FAB auth manager with basic
auth, DAGs paused at creation = false, example DAGs off. DAGs and `include/`
are bind-mounted from the host working tree (`./chain-feeder/dags`,
`./chain-feeder/include`), so DAG changes are picked up without a rebuild.

**Config/secrets:** two env files loaded via `env_file` — `.env.config`
(public, tracked) and `.env.secrets` (gitignored). The `.env` *directory* is
also bind-mounted into the API container at `/app/.env` and loaded by
`main.py` via `load_dotenv(ROOT_DIR/.env)`.

### 3.2 Pain points being addressed

- **Bind mounts in prod.** `chaintelligence-server` mounts `./api`, `./web`,
  `./chain-feeder`, `./config` from the host. Production thus depends on a
  checked-out repo on the deploy host; there is no immutable image.
- **Single Postgres volume.** No replicas, no WAL archiving, no scheduled
  backups. Both the warehouse and Airflow metadata share one logical DB and
  one volume.
- **No autoscaling on the API.** The streaming `/api/routes/analyze` endpoint
  offloads DB fetches to worker threads but the whole process is one
  container — concurrent users compete for one CPU pool.
- **Airflow `LocalExecutor`.** Scheduler-internal concurrency only; no
  multi-worker fan-out for the heavy RPC/graph ingestion DAGs.
- **No TLS, no Ingress.** Airflow UI and the API are exposed as raw NodePorts
  (`8081`, `8000`) over HTTP.
- **No environment separation.** The same `.env.secrets` file is loaded in dev
  and prod; secrets are passed around as files.

---

## 4. Proposed Design

### 4.1 Repository layout

```
deploy/
  helm/
    Chart.yaml                      # parent chart
    values.yaml                     # shared defaults
    values-dev.yaml                 # k3d/kind overlay
    values-prod.yaml                # managed-cloud overlay
    templates/
      chaintelligence-api.yaml      # Deployment + Service + HPA
      configmaps.yaml               # .env.config + config/*.yaml
      externalsecrets.yaml          # .env.secrets (prod) / Secret (dev)
      ingress.yaml                  # Ingress (api + airflow)
      postgres-cluster.yaml         # CloudNativePG Cluster + init_db ConfigMap
      airflow.yaml                  # Airflow Helm subchart values / overrides
  k3d/                              # local cluster bootstrap script + config
  ci/                               # image build + helm lint/dep steps
```

The Airflow official Helm chart is pulled in as a **subchart dependency**
(`dependencies:` in `Chart.yaml`) so its templates are rendered together with
the API/CNPG/Ingress templates and configured via the parent's `values.yaml`.

### 4.2 Namespaces

- `chaintelligence` — API, frontend assets, config, secrets, Ingress.
- `chaintelligence-airflow` — Airflow webserver/scheduler/dag-processor
  (the Helm subchart's target namespace; keeps RBAC and resources isolated).
- `chaintelligence-db` — CloudNativePG cluster and its operator-managed
  resources (PodMonitor, backups, etc.).

Namespaces are created by the chart's `Namespace` templates (or assumed
pre-existing in prod, per cluster policy).

### 4.3 Postgres — CloudNativePG

A single CNPG `Cluster` backs both the warehouse and Airflow metadata:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: chaintelligence-pg
  namespace: chaintelligence-db
spec:
  instances: 1                       # dev
  # instances: 3                     # prod
  imageName: pgvector/pgvector:pg17  # pg17 to match compose; pgvector optional
  storage:
    storageClass: local-path         # dev
    # storageClass: gp3              # prod (EBS) / premium-rwo (GKE) / etc.
    size: 10Gi                       # dev; prod: 200Gi+
  bootstrap:
    initdb:
      database: airflow
      owner: airflow
      postInitSQL:                   # create warehouse DB + roles
        - CREATE DATABASE chaintelligence;
        - CREATE ROLE chaintelligence LOGIN;
      postInitApplicationSQL:
        - \c chaintelligence
        - |                          # init_db.sql contents via ConfigMap ref
  backup:                            # prod only
    barmanObjectStore:
      destinationPath: s3://...
      ...
  wal: { enabled: true, archive: true }   # prod
```

- `init_db.sql` is mounted from a ConfigMap and applied during bootstrap
  (CNPG `postInitApplicationSQL` referencing a ConfigMap volume, or a one-shot
  `Job` that runs psql against the new cluster). Existing sibling migration
  `.sql` files (`add_pool_address.sql`, etc.) run as a `Job` sequenced after
  bootstrap, reusing the existing `chain-feeder/include/sql/` directory baked
  into a small init image.
- Connection strings: Airflow and the API both receive
  `postgresql://<user>:<pwd>@chaintelligence-pg-rw.chaintelligence-db:5432/<db>`
  via the secret CNPG auto-generates (`Cluster` → `Secret` with `user`/`password`
  /`host`/`port`/`dbname` keys). No hardcoded `localhost:5433`.

### 4.4 Airflow — official Helm chart (subchart)

`deploy/helm/templates/airflow.yaml` (or `values.yaml` under the `airflow`
subchart key) configures:

- `executor: LocalExecutor` initially (flippable to `CeleryExecutor` + Redis
  later — see Open Questions).
- `dsn` / `data.metadataSecretName` pointing at the CNPG-generated secret for
  the `airflow` database.
- `dags.gitSync.enabled: true` with `repo`, `branch`, `subPath: chain-feeder`
  — DAGs come from the git repo, no bind-mount, no image rebuild to ship a
  DAG change. (Local dev can instead mount a hostPath for live editing.)
- `config.authManager` → FAB with basic auth, matching current compose.
- Webserver exposed behind the shared Ingress at `/airflow`.
- Resource requests/limits sized per environment; prod runs ≥2 webserver and
  ≥2 scheduler replicas (scheduler leader election via
  `AIRFLOW__SCHEDULER__STANDALONE_DAG_PROCESSOR` / job lock).

### 4.5 FastAPI portal — Deployment + Service + HPA

- Image: existing `Dockerfile` (python:3.13-slim), unchanged in structure but
  the bind-mounts are removed — `api/`, `web/`, `chain-feeder/routing/`,
  `config/` are `COPY`ed at build time (dev: a `gitSync`/`pvc` overlay or a
  dev image with live-reload can mount source for iteration).
- `Deployment` with `replicas: 1` (dev) / `3` (prod), `livenessProbe` on
  `/docs` or a dedicated `/healthz`, `readinessProbe` on the same.
- `Service` (ClusterIP) fronted by the Ingress.
- `HorizontalPodAutoscaler` (prod): target CPU 70%, optional RPS via
  ingress-controller metrics.
- Config/secrets injected as `envFrom: configMapRef` + `secretRef`, plus
  `config/` mounted as a ConfigMap volume at `/app/config` (so
  `dex_config.yaml` and `chains.yaml` resolve as today).
- The `.env` directory behavior (`load_dotenv(ROOT_DIR/.env)`) is replaced:
  `ROOT_DIR/.env` is a ConfigMap/Secret projection in both envs, or the app
  is weaned off the file in favor of real env vars (tracked as a follow-up;
  not required for migration).

### 4.6 Ingress and TLS

- `ingress-nginx` as the Ingress controller (prod: the cloud's managed
  ingress or nginx; dev: nginx installed into k3d).
- `cert-manager` + an `Issuer` (Let's Encrypt prod in prod, self-signed in
  dev) issues a certificate for the API host.
- Two `Ingress` rules (or one with multiple paths):
  - `/` → `chaintelligence-server` Service (the API + static `web/`).
  - `/airflow` → `airflow-webserver` Service (Airflow UI + REST API).
- Postgres has **no Ingress and no NodePort** in prod; the host port `5433`
  is dropped. Local dev that needs psql access uses `kubectl port-forward`.

### 4.7 Secrets and config

| Current | K8s equivalent |
|---|---|
| `.env.config` (tracked) | `ConfigMap` `chaintelligence-config` |
| `.env.secrets` (gitignored) | `ExternalSecret` → cloud secrets manager (prod); `Secret` (dev, generated from a sealed-secret or a local file) |
| `config/dex_config.yaml`, `config/chains.yaml` | `ConfigMap` `chaintelligence-files` mounted at `/app/config` |
| `.env` directory mounted at `/app/.env` | `ConfigMap`+`Secret` projected to `/app/.env` (or replaced by env vars) |
| Airflow FAB admin password | ExternalSecret (prod) / Secret (dev) |
| `GRAPH_API_KEY`, `CMC_API_KEY`, `CRYPTOCOMPARE_API_KEY`, `ZAPPER_AUTH_HEADER`, `RPC_URL`, `DATA_WAREHOUSE_DB`, `PORTAL_*` | ExternalSecret keys, each materialized as env vars on the relevant Deployment |

### 4.8 DAG delivery and DAG code

- **Prod:** `gitSync` sidecar clones the repo into `/opt/airflow/dags` (and
  `/opt/airflow/include` if needed via a second sync or a shared image).
  DAG changes ship by pushing to the tracked branch — no image rebuild.
- **Dev:** optional hostPath/PVC mount of `./chain-feeder/dags` for live
  editing, gated by `values-dev.yaml`.
- The `include/` API clients and `routing/` shared logic are **baked into the
  Airflow image** (`Dockerfile.airflow` already installs extras; we extend it
  to `COPY chain-feeder/include` and `chain-feeder/routing`). This matches the
  API image, which already copies `routing/`.

### 4.9 Observability

- Logs: all workloads log to stdout (Airflow already does; the API uses
  uvicorn's default). Cluster log collection (Loki/Cloud Logging) is out of
  scope to set up but the design ensures nothing writes only to files.
- Metrics: Airflow exposes Prometheus metrics natively; the API gets a
  `/metrics` endpoint (or a prometheus-fastapi middleware) as a small
  follow-up — noted, not required for cutover.
- Health: `liveness`/`readiness` probes on the API and Airflow webserver;
  CNPG manages its own health.

---

## 5. Environment Strategy

| Dimension | Dev (`values-dev.yaml`, k3d/kind) | Prod (`values-prod.yaml`, managed cloud) |
|---|---|---|
| Cluster | `k3d create` / `kind` | EKS / GKE / AKS |
| Namespaces | same three, on the local cluster | same three |
| Postgres instances | 1 (`local-path`, 10Gi) | 3 (cloud disk, 200Gi+, WAL + backups) |
| Postgres exposed | `port-forward` only | in-cluster only |
| Airflow executor | `LocalExecutor` | `LocalExecutor` (Celery deferred) |
| Airflow replicas (web/sched/dag-proc) | 1 / 1 / 1 | 2 / 2 / 2 |
| API replicas | 1 | 3 + HPA |
| API image | dev image with source mount, or `:latest` from CI | pinned semver from CI |
| DAG delivery | hostPath mount of `./chain-feeder/dags` | `gitSync` from tracked branch |
| Config | `ConfigMap` from `.env.config` | same |
| Secrets | plain `Secret` (local) | `ExternalSecret` → cloud secrets manager |
| Ingress / TLS | nginx, self-signed | nginx, Let's Encrypt (cert-manager) |
| Backups | none | CNPG scheduled backups + WAL to object store |
| Resources | small requests, no limits | sized requests + limits |

`helm upgrade --install chaintelligence deploy/helm -f
deploy/helm/values-dev.yaml -n chaintelligence` is the dev equivalent of
`docker-compose up -d`. The prod install is the same command with
`values-prod.yaml` and a real kubecontext.

---

## 6. Migration Strategy

The migration is phased so that each step is independently shippable and
reversible. No phase cuts over until the previous one is verified.

### Phase 0 — Containerize properly (no behavior change)
- Extend `Dockerfile` to `COPY config/` (currently bind-mounted) so the API
  image is self-contained.
- Extend `Dockerfile.airflow` to `COPY chain-feeder/include` and
  `chain-feeder/routing` so the Airflow image is self-contained.
- Keep `docker-compose.yaml` working throughout (bind-mounts still override
  the baked copies in dev).

### Phase 1 — Build the Helm chart, prove dev parity
- Stand up `deploy/helm` with all templates above.
- Stand up `k3d`/`kind` bootstrap in `deploy/k3d/`.
- Install CNPG operator + the chart with `values-dev.yaml` on k3d.
- Verify: API serves `/docs`, Airflow UI loads at `/airflow`, a representative
  DAG run completes, `init_db.sql` applied, a routing-analysis request
  streams end to end.

### Phase 2 — Secrets and config externalization
- Replace `.env.secrets` reads with env-var reads already supported by the
  app (it already uses `os.environ` / `load_dotenv`).
- Add the `ExternalSecret` (prod) and dev `Secret` templates.
- Verify parity: same env vars present in the running pods as the compose
  containers had.

### Phase 3 — Prod cluster bootstrap
- Provision the managed cluster (EKS/GKE/AKS) and install: CNPG operator,
  cert-manager, ingress-nginx, ExternalSecrets Operator, the Airflow chart's
  CRDs (none beyond Airflow's own).
- `helm install` with `values-prod.yaml` against an **empty** warehouse.
- Verify: cluster healthy, backups succeeding, TLS valid, Ingress reachable.

### Phase 4 — Data migration
- Take a consistent dump of the compose Postgres
  (`pg_dump --format=custom` of both `airflow` and `chaintelligence` DBs).
- Restore into the CNPG cluster (dev first, then prod) via a `Job` running
  `pg_restore`, or by seeding a CNPG `recovery` bootstrap from the dump.
- Re-run any sibling migration `.sql` files that post-date the dump.
- Validate row counts on `swaps`, `coin`, `liquidity_pool`, and Airflow
  metadata tables.

### Phase 5 — Cutover
- Point DNS / the cloud load balancer at the new Ingress.
- Stop the compose scheduler, let in-flight DAG runs drain, take a final
  WAL catch-up dump if needed, switch the API and Airflow traffic.
- Keep the compose stack warm for one rollback window, then decommission.

### Rollback
- Until Phase 5 DNS cutover, the compose stack remains the source of truth;
  rollback is "do nothing." After cutover, rollback is: revert DNS, restart
  compose scheduler, replay any writes captured on the K8s side (acceptable
  for this workload — DAG runs are idempotent; swap ingestion is append-only
  keyed on tx hash + log index).

---

## 7. Files Affected

**New:**
- `deploy/helm/Chart.yaml`, `values.yaml`, `values-dev.yaml`,
  `values-prod.yaml`
- `deploy/helm/templates/*.yaml` (api, configmaps, externalsecrets, ingress,
  postgres-cluster, airflow, namespaces)
- `deploy/k3d/create-cluster.sh` and config
- `deploy/ci/` build + helm lint steps (or `.github/workflows/` entries)
- A small `deploy/sql-init/` image or ConfigMap bundling
  `chain-feeder/include/sql/*.sql`

**Modified:**
- `Dockerfile` — add `COPY config/`; remove reliance on bind-mounts at rest.
- `Dockerfile.airflow` — `COPY chain-feeder/include`, `chain-feeder/routing`.
- `api/main.py` — minor: stop depending on `ROOT_DIR/.env` as a directory
  (read env vars directly); keep `load_dotenv` as a fallback. Optional,
  tracked as a follow-up.
- `chain-feeder/routing/config.py` — `DATA_WAREHOUSE_DB` already reads env;
  confirm the CNPG-provided connection string is honored (it is, via env).
- `docker-compose.yaml` — **kept** as the local-only dev option during the
  transition; eventually archived once k3d parity is proven.
- `CLAUDE.md` — update the "Commands" section with the `helm` install lines
  and the k3d bootstrap.

**Unchanged:**
- All application code under `api/`, `chain-feeder/dags/`,
  `chain-feeder/routing/`, `web/`.
- `chain-feeder/include/sql/init_db.sql` and sibling migrations (consumed, not
  rewritten).
- `config/dex_config.yaml`, `config/chains.yaml` (mounted, not relocated).

---

## 8. Verification Plan

### 8.1 Dev parity (end of Phase 1)
- [ ] `helm install` on k3d reaches `Ready` for all Deployments and the CNPG
  `Cluster`.
- [ ] `kubectl port-forward` to the API and a `curl /docs` returns 200.
- [ ] Airflow UI reachable at `/airflow`, login works with the dev secret.
- [ ] `init_db.sql` applied — `\dt` in the `chaintelligence` DB shows the
  expected tables.
- [ ] One DAG run per ingestion family completes: CMC price tier, The Graph
  V3/V4 swap sync, Zapper LP, RPC claim backfill.
- [ ] `/api/routes/analyze` streams progress + result NDJSON for a known
  date range and reproduces a route previously seen in compose.

### 8.2 Prod readiness (end of Phase 3)
- [ ] CNPG cluster `3/3` instances healthy, a backup has completed, a WAL
  segment is archived.
- [ ] cert-manager issued a valid certificate; Ingress serves over HTTPS.
- [ ] ExternalSecret has bound all keys; pods have non-empty `GRAPH_API_KEY`,
  `CMC_API_KEY`, etc.
- [ ] HPA targets the API Deployment; a load test scales replicas.
- [ ] Rolling restart of the API Deployment causes no failed requests beyond
  the drain window.

### 8.3 Data migration (Phase 4)
- [ ] Row counts of `swaps`, `coin`, `coin_contract`, `liquidity_pool`,
  `position_events` match source ±0 (append-only tables).
- [ ] Airflow metadata (`dag_run`, `task_instance`) restored; in-flight DAG
  runs reconciled.
- [ ] A routing-analysis result on the restored data matches a baseline
  result from compose.

### 8.4 Cutover (Phase 5)
- [ ] DNS switched; old compose traffic drops to zero.
- [ ] No errors in API/Airflow logs above baseline for 1 hour post-cutover.
- [ ] Rollback drill: revert DNS and confirm compose is still functional
  within the rollback window.

---

## 9. Open Questions

1. **Airflow executor.** Start on `LocalExecutor` (matches today) or go
   straight to `CeleryExecutor` + Redis to fan out the heavy RPC/graph DAGs?
   Recommendation: start on `LocalExecutor` for parity, flip later — the Helm
   chart makes this a values change.
2. **Postgres long-term.** Stay in-cluster (CNPG) or move to a managed DB
   (RDS / Cloud SQL / Azure Postgres) once the warehouse grows further?
   CNPG is fine through ~hundreds of GB; revisit at the next storage threshold.
3. **DAG `gitSync` repo.** Which repo/branch does `gitSync` track? If the
   DAGs live in this same repo (they do today), `gitSync` points here with
   `subPath: chain-feeder`. Confirm the prod branch name and access
   credentials (deploy key vs. public repo).
4. **Secrets backend.** AWS Secrets Manager vs. GCP Secret Manager vs. Azure
   Key Vault — decided by which managed cloud prod lands on. Dev should not
   depend on it (plain `Secret`).
5. **`.env` directory file.** `main.py` calls `load_dotenv(ROOT_DIR/.env)`.
   Keep projecting that file from a ConfigMap/Secret, or refactor the app to
   read env vars only? Recommendation: project the file for the migration,
   refactor as a follow-up.
6. **API image dev loop.** For local dev on k3d, do we use a source-mounted
   dev image (fast iteration, parity break) or rebuild-on-change (slower,
   full parity)? Recommendation: source mount in dev only, behind
   `values-dev.yaml`.
7. **Frontend assets.** Keep serving `web/` from the API container (today's
   model) or split to a separate static-serving Deployment / CDN? Keep in the
   API container for the migration; CDN is a later optimization.
8. **Resource sizing.** Concrete CPU/memory requests/limits for the API,
   scheduler, dag-processor, and Postgres need to be set from observed
   `docker stats` / k3d metrics during Phase 1 — left as a values pass once
   we have real numbers.
