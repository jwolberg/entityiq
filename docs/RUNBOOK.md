# Dev Runbook

Local setup and run instructions for EntityIQ (backend + frontend monorepo).
Reflects the codebase as of Phase 2 (operator workbench, reporting + integration
API, service-credential auth).

For what the system is and how it's structured, see [ARCHITECTURE.md](./ARCHITECTURE.md);
for build status see [BUILD_PLAN.md](./BUILD_PLAN.md).

---

## Prerequisites

- **Python ≥ 3.11** (backend; `requires-python = ">=3.11"`)
- **Node ≥ 18** + npm (frontend; Vite 5 / React 18)
- **Optional for the full stack:** PostgreSQL 14+ and Redis 6+, or Docker +
  Docker Compose (runs both for you — see "Full stack" below). Neither is
  required for the quick-start path below — the backend defaults to Postgres
  but runs on SQLite for dev, and the pipeline can run inline without a
  Celery worker/Redis.

---

## Repository layout

```
backend/    FastAPI app, pipeline, adapters, scoring, auth, Alembic migrations
            Dockerfile, .dockerignore (ticket 0025)
frontend/   React + TypeScript + Vite operator app
            Dockerfile, .dockerignore (ticket 0025)
shared/     OpenAPI contract + generated TS types (placeholder)
tests/      cross-cutting / e2e (placeholder)
docs/       PRD, STRATEGY, ARCHITECTURE, USERS, BUILD_PLAN, this runbook
docker-compose.yml   Full stack: Postgres + Redis + API + worker + UI
```

---

## One-command demo

```bash
./scripts/demo.sh
```

This creates the backend venv and installs the frontend on the first run,
migrates a local SQLite database (`backend/entityiq-demo.db`), seeds demo
accounts and an integration API key, loads six fictional demo companies that
span all three triage tiers (`python -m app.demo_data`: real pipeline, recorded
source responses, no network), then serves:

- UI: http://localhost:5173. Sign in as `operator@demo.entityiq.dev` or
  `lead@demo.entityiq.dev`; the password for both is `entityiq-demo`.
- API docs: http://localhost:8000/docs

Ctrl-C stops both servers. Re-running is safe: the seed is idempotent. Ports
and the DB path can be overridden with `API_PORT`, `UI_PORT`, and `DEMO_DB`.


The demo also loads **Individual Screening** data: a fictional watchlist
(source `demo_watchlist`) and four fictional people screened through the real
pipeline, covering MATCH, REVIEW (name-only and DOB-conflict) and an auto-CLEAR.
Open **Individuals** in the nav. Screening encrypts subject data. If
`ENTITYIQ_SCREENING_MASTER_KEY` is unset, the script creates a local key in
`backend/.screening-demo.key` (gitignored) and reuses it on later runs. Sign in
as `examiner@demo.entityiq.dev` (same password) to see the read-only examiner
view.
---

## Quick start (no Postgres or Redis needed)

This is the fastest path to a running backend + frontend for development. It uses
SQLite for storage and runs the verification pipeline inline (Celery eager mode).

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Use SQLite + run the pipeline inline (no Redis/worker required)
export DATABASE_URL="sqlite:///./entityiq-dev.db"
export CELERY_TASK_ALWAYS_EAGER=true

# Create the schema
alembic upgrade head

# Run the API (http://localhost:8000)
uvicorn app.main:app --reload --port 8000
```

Verify it's up:

```bash
curl -s http://localhost:8000/health
# {"status":"ok","service":"entityiq-backend","version":"0.1.0"}
```

Interactive API docs (Swagger UI, always reflects the current schema):
**http://localhost:8000/docs**

### 2. Seed demo accounts and an API key

With the backend venv active and the same `DATABASE_URL` exported, from
`backend/`:

```bash
python -m app.seed
```

This creates `operator@demo.entityiq.dev` (role `operator`) and
`lead@demo.entityiq.dev` (role `lead`), both with password `entityiq-demo`, plus
a `demo-integration` service credential. The API key is printed **only on
first creation** (keys are stored hashed). It's idempotent; never run it
against a shared database.

`POST /submissions` accepts that key or an operator `Bearer` token.

Send the printed key as the `X-API-Key` header:

```bash
curl -s -X POST http://localhost:8000/submissions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <key-from-above>" \
  -d '{"company_name":"Acme","work_email":"cto@acme.example",
       "company_domain":"acme.example","country":"US"}'
# 202 Accepted → {"submission_id": "...", "run_id": "...", "status": "pending", ...}
```

Without the key the endpoint returns **401**. The report export endpoint accepts
either an `X-API-Key` (integrating system) or an operator `Bearer` token.

### 3. Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173
```

The dev server proxies `/api` → `http://localhost:8000`, so run the backend
alongside it. Open http://localhost:5173 and sign in with the seeded operator.

> Note: the browser end-to-end wiring (FE ↔ BE through the proxy) is exercised by
> mocked-fetch component tests but not yet by an automated full-stack e2e test —
> confirm the live flow in the browser. See `docs/implementation.md` →
> "Manual verification path".

---

## Full stack (Postgres + Redis + API + worker + UI)

Closer to production than the quick-start path: real Postgres, a real Redis
(broker + operator sessions), an out-of-process Celery worker, and the UI —
all via `docker-compose.yml` at the repo root (ticket 0025).

### Docker Compose (recommended)

Needs a running Docker daemon (`docker info` succeeds).

```bash
# from the repo root
docker compose build
docker compose up -d
docker compose ps          # all 5 services should be Up (db/redis "healthy")
```

`docker compose up` runs `alembic upgrade head` against Postgres automatically
(the `api` service's start command) before serving. Then seed demo accounts +
an integration API key (prints the key once):

```bash
# The compose DB is Postgres, so the demo seed needs the explicit opt-in
# (it creates accounts with a public password; only do this on a throwaway DB).
docker compose exec -T -e ENTITYIQ_ALLOW_DEMO_SEED=1 api python -m app.seed
```

Verify:

- API: http://localhost:8000/docs (health: `curl -s localhost:8000/health`)
- UI: http://localhost:5173 — sign in with `operator@demo.entityiq.dev` /
  `entityiq-demo` (password from the seed step)

Submit a registration (replace the key with the one `app.seed` printed) and
watch it complete via the **real, non-eager Celery worker** on Postgres —
this is the isolated-stage-session path from ticket 0001, exercised for real
here instead of on SQLite:

```bash
curl -s -X POST http://localhost:8000/submissions \
  -H "Content-Type: application/json" -H "X-API-Key: <key>" \
  -d '{"company_name":"Acme Corp","work_email":"cto@acme.example",
       "company_domain":"acme.example","country":"US"}'
# {"run_id": "...", "status": "pending", ...}

curl -s "http://localhost:8000/reports/<run_id>" -H "X-API-Key: <key>" | python3 -m json.tool
# poll until "status": "complete" / run.status == "complete"
```

This submission uses the *real* adapters (no recorded fixtures — that's only
for tests/demo_data), so registries/sanctions/WHOIS/geocode calls go out over
the network; sources without a configured token/provider
(`OPENCORPORATES_API_TOKEN`, `ENTITYIQ_TAX_ID_PROVIDER`,
`ENTITYIQ_LINKEDIN_PROVIDER`) report as `unavailable` per-source rather than
failing the run (P4-T3) — the run still reaches `complete`.

Logs / rebuild after code changes / teardown:

```bash
docker compose logs -f api      # or worker, ui, db, redis
docker compose build api worker # after backend changes (image isn't live-mounted)
docker compose down             # stop; add -v to also drop the Postgres volume
```

Compose file summary (`docker-compose.yml`):

| Service | Image / build | Notes |
| --- | --- | --- |
| `db` | `postgres:16-alpine` | `entityiq`/`entityiq`/`entityiq`; healthcheck gates `api`/`worker` startup. |
| `redis` | `redis:7-alpine` | Celery broker/result-backend **and** the operator session store (ticket 0024). |
| `api` | `backend/Dockerfile` | Runs `alembic upgrade head` then `uvicorn`, port 8000. |
| `worker` | `backend/Dockerfile` (same image, different command) | `celery -A app.worker.celery_app worker --loglevel=info`, non-eager. |
| `ui` | `frontend/Dockerfile` | Vite dev server, port 5173, proxies `/api` to `api:8000`. |

The `backend`/`frontend` images are rebuilt from source, not live-mounted —
re-run `docker compose build` after editing code (the `--reload`/HMR dev loop
is the SQLite quick-start path above, not this one).

### Manual (no Docker): Postgres + Celery worker on the host

Same topology without containers — useful if Docker isn't available.

```bash
# default expected by the app:
# postgresql+psycopg://entityiq:entityiq@localhost:5432/entityiq
createuser entityiq --pwprompt        # password: entityiq
createdb entityiq -O entityiq
```

```bash
cd backend
source .venv/bin/activate
export DATABASE_URL="postgresql+psycopg://entityiq:entityiq@localhost:5432/entityiq"
export REDIS_URL="redis://localhost:6379/0"     # operator sessions (ticket 0024)
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

With Redis running on `localhost:6379` (do NOT set `CELERY_TASK_ALWAYS_EAGER`):

```bash
cd backend
source .venv/bin/activate
celery -A app.worker.celery_app worker --loglevel=info
```

The API enqueues verification runs to Redis; the worker executes the pipeline.

---

## Individual screening: lists and monitoring

Under docker-compose, export `ENTITYIQ_SCREENING_MASTER_KEY` before
`docker compose up`. Without it, `POST /screenings` returns 503. The worker
consumes both the default and `screening` queues. Verified 2026-09-24:
compose on Postgres 16, OFAC ingested in the container, two screenings
through the real worker (~1 s each), replay reproduced, examiner writes 403,
and Postgres rejected an UPDATE on screening_decision.

Load (or refresh) the official sanctions lists:

```bash
cd backend && .venv/bin/python -m app.lists.ingest          # OFAC, UN, EU, UK
.venv/bin/python -m app.lists.ingest ofac_sdn               # one source
```

Each list is stored as a versioned snapshot only when its content changed.
Every new snapshot triggers monitoring: active subjects whose names hit the
added or changed records get a new `monitoring` run. Schedule it daily
(cron, or a Celery beat entry). A fetch failure leaves the last good
snapshot in use. Live smoke run (2026-09-24): about 30 s for all four lists,
16.6k individuals; each screening took about 2 s against them.

Crypto-shred retention for screening subjects (ADR-0004), also scheduled:

```bash
.venv/bin/python -m app.screening.retention
```

---

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://entityiq:entityiq@localhost:5432/entityiq` | SQLAlchemy/Alembic connection. Use `sqlite:///./entityiq-dev.db` for quick dev. |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | `true` runs the pipeline inline in the API process — no Redis/worker needed. |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Celery broker (when not in eager mode). |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Celery result backend. |
| `OPENCORPORATES_API_TOKEN` | _(unset)_ | OpenCorporates API token. **Required for registry lookups**: the API returns 401 without one, and the registry source then shows as *unavailable* in the report. Production use also needs a license (Open Decision #5). |
| `SESSION_TTL_HOURS` | `12` | Operator session lifetime, enforced by whichever session store is active (Redis TTL or the in-memory store's own expiry check). |
| `REDIS_URL` | `redis://localhost:6379/0` | Backend for operator sessions (ticket 0024). Tried once at API startup; sessions are then shared across API instances and survive a restart. If unreachable, falls back to a single-process in-memory store and logs a startup warning — dev/demo work either way. Independent of `CELERY_BROKER_URL` (can point at the same Redis or a different one/DB). |
| `IPINFO_TOKEN` | _(unset)_ | Optional ipinfo.io token. The free tier works without one; a paid token adds precise VPN/proxy/hosting flags. |
| `ENTITYIQ_STAGE_TIMEOUT_SECONDS` | `600` | Per-stage budget in the Celery worker. An overrunning stage is marked unavailable and its writes are discarded. |
| `ENTITYIQ_RUN_TIMEOUT_SECONDS` | `5400` | Whole-run budget (PRD: under 2h). Once spent, remaining sources are skipped; scoring and the report still run. Celery's soft limit is this + 15 min, the hard limit + 20 min. |
| `ENTITYIQ_ADAPTER_MAX_ATTEMPTS` | `3` | Total calls per source on `timeout`/`unavailable` (1 = no retry). `not_found` and `rate_limited` are never retried. |
| `ENTITYIQ_ADAPTER_RETRY_BACKOFF_SECONDS` | `1.0` | First retry delay; doubles each retry. |
| `ENTITYIQ_TAX_ID_PROVIDER` | _(unset)_ | Tax-ID/FEIN verification provider. Unset = none configured: the `verify_tax_id` source reports *unavailable* (no signal, no penalty). `stub` = deterministic fictional records for demos. A live provider waits on IC0-T1 (Open Decision #5).
| `ENTITYIQ_LINKEDIN_PROVIDER` | _(unset)_ | LinkedIn company-page data provider. Unset = none configured: `verify_linkedin` reports *unavailable* (no signal, no penalty). `stub` = deterministic fictional pages for demos. No scraping path exists; a live provider waits on IC0-T2.
| `ENTITYIQ_SCREENING_MASTER_KEY` | _(unset; required for screening)_ | Base64 32-byte master key that wraps each screening subject's data key (ADR-0004). Generate with `python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"`. Losing it makes all subject PII unrecoverable; keep it in the secret manager. |
| `ENTITYIQ_SCREENING_RETENTION_DAYS` | `1825` | Days after a subject's relationship ends before `python -m app.screening.retention` crypto-shreds it (ADR-0004). |
| `ENTITYIQ_SCREENING_STAGE_TIMEOUT_SECONDS` | `10` | Per-stage budget for individual screening (separate from KYB's). |
| `ENTITYIQ_SCREENING_RUN_TIMEOUT_SECONDS` | `60` | Whole-run budget for individual screening. Celery soft limit is this + 30 s, hard + 45 s. Screening tasks run on the `screening` queue: start a worker with `celery -A app.worker worker -Q screening` (and one for the default queue for KYB).
| `TRUSTED_PROXY_DEPTH` | `0` | Hops to walk back from the right of `X-Forwarded-For` to find the client IP. `0` = use the direct connection peer (correct when not behind a proxy). Set to the number of trusted proxies in front of the app. |
| `ENTITYIQ_RETENTION_NETWORK_DAYS` | `90` | Days a submission's raw network metadata (`source_ip`/`user_agent`/`forwarded_headers`) is kept before the retention job truncates/nulls it (ADR-0002). |
| `ENTITYIQ_RETENTION_REVIEWED_DAYS` | `1825` | Days after a review decision before the retention job nulls a reviewed submission's PII (ADR-0002). |
| `ENTITYIQ_RETENTION_UNREVIEWED_DAYS` | `180` | Days after submission before the retention job nulls a never-reviewed submission's PII (ADR-0002). |

---

## Tests and linting

### Backend (from `backend/`, venv active)

```bash
ruff check .            # lint
ruff format --check .   # formatting
pytest -q               # tests (run on SQLite; no Postgres/Redis needed)
```

### Frontend (from `frontend/`)

```bash
npm run lint            # eslint (max-warnings 0)
npm run test            # vitest
npm run build           # type-check + production build
```

CI (GitHub Actions, `.github/workflows/ci.yml`) runs all of the above plus the frontend build on every push to main and every PR.

---

## Common notes & gotchas

- **SQLite vs Postgres:** models use portable column types, so tests and quick-dev
  run on SQLite. Production targets Postgres — run `alembic upgrade head` against a
  real Postgres before relying on Postgres-specific behavior.
- **Run backend commands from `backend/`** so the `app` package is importable
  (`uvicorn app.main:app`, `alembic …`, `celery -A app.worker.celery_app …`).
- **OpenCorporates adapter** uses the public API only. Production use requires a
  license agreement (Open Decision #5, unresolved) — do not point it at a paid
  plan/key without resolving that first.
- **Auth is MVP-grade:** opaque bearer tokens (PBKDF2 password hashing), a
  Redis-backed session store with an in-memory fallback (ticket 0024,
  `REDIS_URL`), TTL enforced via `SESSION_TTL_HOURS`. No JWT/OIDC yet — see
  `app/auth/operator.py` docstring for the swap-in path.
- **Reset local dev DB:** delete the SQLite file (`backend/entityiq-dev.db`) and
  re-run `alembic upgrade head`. The DB file and `.venv/`, `node_modules/` are
  gitignored.

---

## Key endpoints (MVP)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness check |
| GET | `/docs` | Interactive OpenAPI (Swagger) UI |
| POST | `/submissions` | Submit a registration (captures network metadata, enqueues a run). **Requires `X-API-Key`.** |
| GET | `/reports` | List analyzed companies (dashboard) |
| GET | `/reports/{id}` | Retrieve a report (partial-result aware) |
| GET | `/reports/{id}/export` | Machine-readable export. **Requires `X-API-Key` or operator Bearer token.** |
| POST | `/auth/sign-in` | Operator sign-in → Bearer token |
| POST | `/reanalysis/{run_id}` | Re-trigger analysis (operator auth; audited) |
| POST | `/workflow/runs/{run_id}/correct` | Correct fields + re-run (operator auth; audited) |
| POST | `/workflow/runs/{run_id}/notes` | Add review notes (operator auth; audited) |
| POST | `/reviews/{run_id}` | Mark reviewed (operator auth required; audited) |
| POST | `/api-clients` | Create an integration API key; full key shown once (**lead only**; audited) |
| GET | `/api-clients` | List integration API keys — prefix + metadata only, never the hash (**lead only**) |
| POST | `/api-clients/{id}/revoke` | Revoke an integration API key; rejected on its next use (**lead only**; audited) |

Exact request/response shapes are in the live `/docs`. Report responses are
role-gated per ADR-0002 (`docs/decisions/0002-pii-retention-policy.md`): leads
see raw network-metadata attribution, operators see the derived signals only,
and integration API keys see neither that nor contact-PII mismatch fields.

---

## PII retention job (ADR-0002)

Anonymizes submissions past their retention window in place — never a hard
delete. Idempotent; safe to run repeatedly.

```
cd backend && python -m app.db.retention
```

Or via Celery (`entityiq.run_retention` in `app/worker.py`) if a worker is
already running — this repo does not wire a beat schedule, so trigger it from
cron/systemd-timer/etc. in a real deployment. Windows are configured via the
`ENTITYIQ_RETENTION_*` env vars above.
