---
title: Deploy to Cloud Run (private demo)
last-verified: not yet
anchor: RB-deploy-cloud-run
---

# Deploy to Cloud Run (private demo)

Ticket 0054, ADR-0005. One Cloud Run service (UI + API) on Cloud SQL
Postgres, private, scale-to-zero. No worker, no Redis.

## [1] Prerequisites

- `gcloud` signed in (`gcloud auth list`) with rights to create projects and
  link billing.
- Run from a clean checkout of the commit you want to ship. The image tag is
  the commit SHA, with `-dirty` added if there are uncommitted changes.
- No Docker needed: images are built by Cloud Build from the root
  `Dockerfile`. `.gcloudignore` limits the upload to the build inputs: no
  node_modules, venvs, local DBs, keys, tests or images.

## [2] First deploy

```bash
PROJECT_ID=entityiq-demo BILLING_ACCOUNT=<billing-account-id> \
  ./scripts/deploy-cloud-run.sh all
```

`all` runs these steps in order. Each can also run on its own.

| Step | What it does |
|---|---|
| `infra` | Creates the project and links billing; enables the Run, SQL Admin, Artifact Registry, Cloud Build and Secret Manager APIs; creates the Artifact Registry repo and the Cloud SQL instance, database and user. Creates the `entityiq-database-url` and `entityiq-screening-master-key` secrets **once**, and the runtime service account with Cloud SQL client and secret access. |
| `build` | Builds the image with Cloud Build and pushes it to `<region>-docker.pkg.dev/<project>/entityiq/app:<sha>`. |
| `migrate` | Cloud Run Job `entityiq-migrate`: `alembic upgrade head`. |
| `seed` | Cloud Run Job `entityiq-seed`, with `ENTITYIQ_ALLOW_DEMO_SEED=1`: demo accounts, 6 companies, 4 individuals. It's idempotent. The job log prints the demo integration API key. |
| `service` | Deploys the `entityiq` service: private, 0–1 instances, 900 s timeout, secrets from Secret Manager. |

Creating the Cloud SQL instance takes several minutes on the first run.

## [3] Open it

```bash
gcloud run services proxy entityiq --project entityiq-demo --region us-central1 --port 8080
```

Then browse to http://localhost:8080. Sign in with `lead@demo.entityiq.dev`,
`operator@demo.entityiq.dev` or `examiner@demo.entityiq.dev`; the password for
all three is `entityiq-demo`. The API is at `http://localhost:8080/api/...`, and
its docs at `/api/docs`.

Sessions are in memory, on one instance. After an idle scale-to-zero or a
redeploy, sign in again.

## [4] Ship a new version

```bash
PROJECT_ID=entityiq-demo ./scripts/deploy-cloud-run.sh build
PROJECT_ID=entityiq-demo ./scripts/deploy-cloud-run.sh migrate   # when migrations changed
PROJECT_ID=entityiq-demo ./scripts/deploy-cloud-run.sh service
```

`build`, `migrate` and `service` must run from the same commit, because they
share the image tag.

## [5] Things that must not happen

- **Never regenerate or delete `entityiq-screening-master-key`.** Without it,
  every screening subject's PII is unreadable (ADR-0004). The script only
  creates it when it's missing.
- **Don't make the service public** (`--allow-unauthenticated`) until the
  read-mostly demo login from ticket 0022 exists. The demo password is public,
  and the lead account can create API keys.
- **Don't seed a database that holds real data.** The seed only runs with
  `ENTITYIQ_ALLOW_DEMO_SEED=1`, and only in the seed job.

## [6] Cost and teardown

- **Idle:** mostly Cloud SQL `db-f1-micro` with 10 GB HDD (about $10/month
  estimated), plus Artifact Registry storage. The service and jobs cost nothing
  when idle.
- **Pause the database:**
  `gcloud sql instances patch entityiq-pg --activation-policy NEVER --project entityiq-demo`.
  Set it back to `ALWAYS` to resume.
- **Remove everything:** `gcloud projects delete entityiq-demo`. This is
  irreversible after the 30-day recovery window, and it deletes the data and
  the master key.

## [7] Scaling up later (config, not code)

- **Shared sessions:** create Memorystore Redis, connect the service with
  Direct VPC egress (`--network`, `--subnet`, `--vpc-egress
  private-ranges-only`), set `REDIS_URL`, and lift `--max-instances`.
- **Background worker:** deploy the same image as a worker pool or an always-on
  service running `celery -A app.worker.celery_app worker -Q celery,screening`.
  Then set `CELERY_TASK_ALWAYS_EAGER=false` and point `CELERY_BROKER_URL` at
  Redis.
