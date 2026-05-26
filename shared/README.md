# shared/

This directory will hold the OpenAPI contract generated from the FastAPI backend
and the TypeScript types generated from that contract for consumption by the
frontend.

Generation tooling and the workflow (FastAPI → openapi.json → TS types) will be
configured in **P0-T3 (Lint, test harness, and CI)**.

Nothing in this directory is hand-maintained; it is produced by the build pipeline.
