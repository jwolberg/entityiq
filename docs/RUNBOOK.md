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
- **Optional for the full stack:** PostgreSQL 14+ and Redis 6+.
  Neither is required for the quick-start path below — the backend defaults to
  Postgres but runs on SQLite for dev, and the pipeline can run inline without a
  Celery worker/Redis.

---

## Repository layout

```
backend/    FastAPI app, pipeline, adapters, scoring, auth, Alembic migrations
frontend/   React + TypeScript + Vite operator app
shared/     OpenAPI contract + generated TS types (placeholder)
tests/      cross-cutting / e2e (placeholder)
docs/       PRD, STRATEGY, ARCHITECTURE, USERS, BUILD_PLAN, this runbook
```

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

### 2. Seed an operator account (needed to sign in)

There is no seed script yet, so create an operator directly. With the backend venv
active and the same `DATABASE_URL` exported, from `backend/`:

```bash
python - <<'PY'
from app.db.session import SessionLocal
from app.models.operator import Operator
from app.auth.operator import hash_password

db = SessionLocal()
db.add(Operator(
    email="operator@example.com",
    full_name="Dev Operator",
    role="lead",                         # "operator" or "lead"
    password_hash=hash_password("changeme"),
))
db.commit()
print("created operator@example.com / changeme")
PY
```

### 3. Seed a service credential (needed to submit via the API)

`POST /submissions` and `GET /reports/{id}/export` require a **service credential**
(API key) — integrating systems authenticate at the API boundary (P2-T12). The
operator UI does not submit, so this is only needed for direct API testing
(curl / Swagger). With the venv active and the same `DATABASE_URL` exported, from
`backend/`:

```bash
python - <<'PY'
from app.db.session import SessionLocal
from app.auth.service import create_api_client

db = SessionLocal()
client, key = create_api_client("local-dev", db)   # plaintext shown ONCE
print(f"X-API-Key: {key}")
PY
```

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

### 4. Frontend

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

## Full stack (Postgres + Redis + Celery worker)

Closer to production: real Postgres, a Redis broker, and an out-of-process worker.

### Postgres

Start Postgres and create the database/user matching the default URL (or point
`DATABASE_URL` at your own):

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
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Redis + Celery worker

With Redis running on `localhost:6379` (do NOT set `CELERY_TASK_ALWAYS_EAGER`):

```bash
cd backend
source .venv/bin/activate
celery -A app.worker.celery_app worker --loglevel=info
```

The API enqueues verification runs to Redis; the worker executes the pipeline.

---

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://entityiq:entityiq@localhost:5432/entityiq` | SQLAlchemy/Alembic connection. Use `sqlite:///./entityiq-dev.db` for quick dev. |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | `true` runs the pipeline inline in the API process — no Redis/worker needed. |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Celery broker (when not in eager mode). |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Celery result backend. |
| `OPENCORPORATES_API_TOKEN` | _(unset)_ | OpenCorporates API token. **Required for registry lookups**: the API returns 401 without one, and the registry source then shows as *unavailable* in the report. Production use also needs a license (Open Decision #5). |
| `IPINFO_TOKEN` | _(unset)_ | Optional ipinfo.io token. The free tier works without one; a paid token adds precise VPN/proxy/hosting flags. |
| `TRUSTED_PROXY_DEPTH` | `0` | Hops to walk back from the right of `X-Forwarded-For` to find the client IP. `0` = use the direct connection peer (correct when not behind a proxy). Set to the number of trusted proxies in front of the app. |

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

CI (`.gitlab-ci.yml`) runs all of the above on push.

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
- **Auth is MVP-grade:** in-memory session store, no token expiry, PBKDF2 hashing.
  Fine for dev; harden (persistent/JWT sessions, TTL) before production.
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

Exact request/response shapes are in the live `/docs`.
