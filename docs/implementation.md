# Implementation

## Scope Implemented
- Requested scope: P1-T1, P1-T2, P1-T3 (three sequential tickets, each committed separately)
- Related phase: Phase 1 — MVP Vertical Slice (walking skeleton)
- Related ticket(s): P1-T1, P1-T2, P1-T3

## Approach
- Implemented tickets strictly in dependency order: P1-T1 → P1-T2 → P1-T3
- Each ticket validated (ruff + pytest) and committed before the next began
- Reused all Phase 0 patterns: SQLAlchemy ORM, in-memory SQLite for tests, exact-version pinning in pyproject.toml
- No scope expansion: adapters, consistency checks, scoring, and report assembly are explicitly deferred per BUILD_PLAN

## Key Decisions
1. **Trusted-IP**: `request.client.host` (TCP peer) is authoritative by default; `TRUSTED_PROXY_DEPTH` env var allows configuring N-hop proxy trust. `X-Forwarded-For` is captured in `forwarded_headers` (context only, never as `source_ip`).
2. **Celery testability**: API tests patch `enqueue_run` to a no-op. Orchestrator tests call `run_sync()` directly. `task_always_eager` retained as env-var option for staging. No live Redis needed in CI.
3. **StaticPool**: SQLite `:memory:` gives each new connection a fresh DB. `StaticPool` forces all test sessions to share one connection so tables created by `create_all()` are visible to API endpoint writes.
4. **normalize.py bootstrapped in P1-T2**: `default_stages()` needed a concrete import at P1-T2 commit time. The full normalize logic was included then; P1-T3 adds tests and refines the docstring.
5. **Entity-per-submission in P1-T1**: Entity resolution is P2-T1. Each submission creates a new entity row as a placeholder.

---

## Implementation Plan (executed)

### P1-T1
1. Create `backend/app/schemas/submission.py` — Pydantic models for request/response
2. Create `backend/app/api/submissions.py` — POST endpoint with trusted-IP logic and free-email detection
3. Create `backend/app/pipeline/orchestrator.py` — stub `enqueue_run` (no-op)
4. Wire router into `app/main.py`
5. Create `backend/tests/conftest.py` — shared SQLite fixtures (StaticPool)
6. Create `backend/tests/test_submissions.py` — 14 tests

### P1-T2
1. Create `backend/app/pipeline/base.py` — `PipelineStage` protocol
2. Rewrite `backend/app/pipeline/orchestrator.py` — `Orchestrator`, `enqueue_run`, `enqueue_reanalysis`
3. Create `backend/app/worker.py` — Celery app + `run_verification_task`
4. Create `backend/app/pipeline/normalize.py` — full normalize logic (stub for P1-T3 tests)
5. Update `tests/conftest.py` — patch `enqueue_run` in `api_client` fixture
6. Create `backend/tests/pipeline/test_orchestrator.py` — 8 tests

### P1-T3
1. Update `normalize.py` docstring — remove "stub" language, finalize design notes
2. Create `backend/tests/pipeline/test_normalize.py` — 32 tests covering helpers + stage

---

## Code Changes

### File: backend/app/schemas/submission.py
- Pydantic `SubmissionRequest` (required + optional fields per PRD) + `SubmissionResponse`
- `model_validator` on `SubmissionRequest` strips scheme/path from `company_domain`
- `EmailStr` for work_email validation (requires `email-validator` package, already present)

### File: backend/app/api/submissions.py
- `POST /submissions` endpoint returning 202
- Trusted-IP: `_get_trusted_client_ip` uses `request.client.host` at depth 0; configurable via `TRUSTED_PROXY_DEPTH`
- Free-email: `_is_free_email_domain` checks against a 30+ domain frozenset; returned in response, not a rejection
- Idempotency: duplicate `idempotency_key` returns existing run without creating new rows
- Network metadata captured server-side: `source_ip`, `user_agent`, `forwarded_headers`, `endpoint`, `submitted_at`
- Creates Entity + Submission + VerificationRun then calls `enqueue_run`

### File: backend/app/pipeline/base.py
- `PipelineStage` runtime-checkable Protocol with `name: str` property and `run(run_id, db, context) -> dict`

### File: backend/app/pipeline/orchestrator.py
- `Orchestrator.run_sync(run_id, db)`: pending->running->complete lifecycle; per-stage `source_availability` committed after each stage; failing stage -> `unavailable`; run always reaches `complete` or `failed`
- `enqueue_run(run_id)`: calls `run_verification_task.delay()`
- `enqueue_reanalysis(entity_id, supersedes_run_id, db)`: creates new run with `supersedes_id`, calls `enqueue_run`
- `default_stages()`: lazily imports and returns `[NormalizeInputStage()]`

### File: backend/app/worker.py
- `celery_app` with Redis broker (configurable via env); `CELERY_TASK_ALWAYS_EAGER` env var
- `run_verification_task(run_id)`: opens `SessionLocal()`, calls `Orchestrator(default_stages()).run_sync()`

### File: backend/app/pipeline/normalize.py
- `normalize_domain()`: lowercase, strip scheme/path/port/query
- `normalize_country()`: ISO2 passthrough or dict lookup across 40+ aliases
- `normalize_email()`: lowercase + strip
- `normalize_address()`: strip whitespace
- `format_tax_id()`: stub — returns stripped value
- `NormalizeInputStage`: reads `run.submission`, calls helpers, returns `{**context, "normalized": {...}}`; no raise on malformed input

### File: backend/tests/conftest.py
- `sqlite_engine` (session scope, StaticPool), `db_session` (function scope, rollback), `api_client` (patches `enqueue_run` + overrides `_get_db`)

### File: backend/tests/test_submissions.py
- 14 tests covering 202 + DB persistence, 422 on missing fields, trusted-IP invariant, idempotency dedup, free-email flag, optional fields, domain normalization

### File: backend/tests/pipeline/test_orchestrator.py
- 8 tests covering pending->complete, all stages complete, raising->unavailable+run completes, all-fail still completes, partial-result visibility, re-analysis supersedes, prior run retained, protocol compliance

### File: backend/tests/pipeline/test_normalize.py
- 32 tests covering domain normalization, country mapping, email normalization, NormalizeInputStage round-trips, and format_tax_id stub

---

## Acceptance Criteria Mapping

- **POST /submissions returns 202 with run_id**: `test_valid_submission_returns_202`
- **Missing required field -> 422**: `test_missing_required_field_returns_422[*]`
- **Trusted peer IP, not XFF**: `test_spoofed_x_forwarded_for_is_not_used_as_source_ip`
- **Forwarded headers stored for context**: `test_forwarded_headers_are_stored_separately`
- **Duplicate idempotency key -> no second run**: `test_idempotent_submission_does_not_create_duplicate`
- **Free/disposable email accepted + flagged**: `test_free_email_domain_accepted_but_flagged`
- **Two-stage run: pending->complete**: `test_two_stage_run_transitions_pending_to_complete`
- **Raising stage -> unavailable, run completes**: `test_raising_stage_is_unavailable_run_still_completes`
- **Partial results readable mid-run**: `test_stage_status_is_readable_after_each_stage`
- **Re-analysis supersedes prior run**: `test_reanalysis_creates_new_run_with_supersedes_id`
- **Mixed-case domain normalised**: `test_normalize_stage_mixed_case_domain`
- **Country mapped to ISO**: `test_normalize_stage_country_mapped_to_iso`
- **Malformed domain no raise**: `test_normalize_stage_malformed_domain_handled`
- **Unsupported country no raise**: `test_normalize_stage_unsupported_country_is_none_not_raised`

---

## Build Plan Mapping

- **P1-T1**: Complete (2026-05-26) — submission endpoint, network metadata, idempotency, free-email flag
- **P1-T2**: Complete (2026-05-26) — orchestrator, Celery worker, stage protocol, re-analysis plumbing
- **P1-T3**: Complete (2026-05-26) — normalize stage with full helper functions and test suite

---

## Validation

All validation run inside the `.venv` (Python 3.10.10, packages pinned):

```
ruff check . --exclude .venv  ->  All checks passed!
ruff format --check . --exclude .venv  ->  34 files already formatted
pytest tests/ -v  ->  63 passed in 0.45s
```

Test breakdown per ticket:
- P1-T1: 14 tests (test_submissions.py)
- P1-T2: 8 tests (pipeline/test_orchestrator.py)
- P1-T3: 32 tests (pipeline/test_normalize.py)
- Pre-existing (P0): 9 tests (test_health.py + test_models.py)

---

## Open Issues

1. **Entity-per-submission** (known tech debt): P1-T1 creates one Entity per submission. Entity deduplication/resolution is P2-T1.

2. **Celery task in API tests is patched, not exercised**: `run_verification_task` is tested synchronously via `run_sync()`. Full Celery path integration test is deferred to P3-T5.

3. **Free-email flag not persisted to DB**: Returned in the 202 response only. P1-T3 normalize stage can persist this as Evidence when warranted.

4. **Country map covers ~40 aliases**: Will grow as Tier 1 adapters (P1-T4) encounter new locales.

---

## BUILD_PLAN Update

- P1-T1: Complete (2026-05-26)
- P1-T2: Complete (2026-05-26)
- P1-T3: Complete (2026-05-26)
- Current ticket updated to: P1-T4 — Tier-1 authoritative source adapter (OpenCorporates)
- Blockers: None (Open decision #5 should be confirmed before starting P1-T4)
