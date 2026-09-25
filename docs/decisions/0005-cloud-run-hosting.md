---
id: 0005
title: Host the demo on Cloud Run with Cloud SQL Postgres, no worker, no Redis
anchor: ADR-0005
status: accepted
date: 2026-09-24
supersedes:
superseded-by:
---

Backlog ticket 0054 (a private hosted demo). This is the first step toward ticket
0022 (a public, read-mostly demo). The owner made these choices on 2026-09-24.

## [1] Context

EntityIQ runs locally as an API, a Celery worker, Postgres, Redis and a Vite dev
server (docker-compose). A hosted demo needs to:

- cost close to nothing when idle;
- keep the fictional demo data only;
- stay private until the read-mostly login from ticket 0022 exists.

The seeded demo accounts share a public password, and the lead account can
create API keys.

## [2] Decision

| Concern | Choice | Why |
|---|---|---|
| Compute | One **Cloud Run service** serves the UI at `/` and the API at `/api/*`, from one image (root `Dockerfile`, `app/ui.py`) | Same paths as the Vite dev proxy. No CORS, and no second service to authenticate between. Scales to zero |
| Background work | **No Celery worker.** `CELERY_TASK_ALWAYS_EAGER=true` runs the pipeline inside the request. Stage and run budgets are 60 s and 600 s, inside a 900 s request timeout | An always-on worker costs about $50/month. The owner declined it. The eager path still enforces stage and run timeouts, because the Orchestrator applies them |
| Database | **Cloud SQL Postgres 16**, `db-f1-micro`, HDD, through the Cloud SQL connector (Unix socket) | Smallest Postgres tier, about $10/month. Needs no VPC |
| Sessions / Redis | **No Memorystore.** The in-memory session store runs with `max-instances=1` | Memorystore's floor is about $36/month (1 GiB Basic). With no worker, Redis only holds sessions, and the app already falls back to memory |
| Secrets | **Secret Manager**: `entityiq-database-url` and `entityiq-screening-master-key`, readable only by the runtime service account | Nothing sensitive goes in the image, the repo or plain env vars. The master key is created once and never regenerated (ADR-0004) |
| Schema and data | **Cloud Run Jobs** from the same image: `entityiq-migrate` (`alembic upgrade head`) and `entityiq-seed` (only with `ENTITYIQ_ALLOW_DEMO_SEED=1`) | Explicit, logged, repeatable steps. The seed guard stays in force everywhere else |
| Access | **Private**: `--no-allow-unauthenticated`. Open it with `gcloud run services proxy` | The public demo password and the unrestricted lead account aren't safe on the open internet |
| Project | A new project, `entityiq-demo`, on the "Side Projects" billing account | Isolated: deleting the project removes everything and stops all charges |

**Rejected:**
- A Cloud Run **worker pool** for Celery. It's GA, but it's an always-on cost, and the local gcloud can't load the command (its bundled Python is missing `grpc`).
- **Sessions in Postgres.** They'd need new code and a migration, with no demo benefit today.

## [3] Consequences

- **Sessions reset** when the instance restarts or scales to zero, so users
  sign in again. There's only one instance, so there's no horizontal scaling.
- **New KYB submissions** run in the request (up to about 10 minutes), using live
  public sources.
  - Adapters without credentials (OpenCorporates, tax-ID and LinkedIn
    providers) report as unavailable, as they do in local dev.
  - Individual screening uses only the fictional `demo_watchlist`. Required
    sources are set to it, and the list-age check is relaxed for its fixed
    snapshot.
- **Idle cost** is mostly Cloud SQL, about $10/month, plus Artifact Registry
  storage. The service and jobs cost nothing when idle.
- **Going public (ticket 0022)** needs a read-mostly demo login first.
- **Scaling up later** means:
  - set `REDIS_URL` (Memorystore through Direct VPC egress) and lift
    `max-instances`;
  - add a worker (a worker pool or an always-on service) and turn off eager
    mode.

  All of these are config changes, not code changes.

Runbook: [`docs/runbooks/deploy-cloud-run.md`](../runbooks/deploy-cloud-run.md).
