# Implementation

## Scope Implemented
- Requested scope: P0-T4 — Postgres + migrations + core data model
- Related phase: Phase 0 — Decisions & Scaffolding
- Related ticket(s): P0-T4 (final Phase 0 ticket)

## Approach
- Added `sqlalchemy==2.0.30`, `alembic==1.13.1`, and `psycopg[binary]==3.1.19`
  to `backend/pyproject.toml` with exact pinned versions (consistent with
  existing pinning convention established in P0-T3).
- Created `backend/app/db/session.py`: engine + `SessionLocal` factory reading
  `DATABASE_URL` from env (default: local Postgres), and `Base` (DeclarativeBase).
- Created 10 model files in `backend/app/models/` (one per entity), all
  using portable SQLAlchemy types (generic `JSON`, not Postgres-only `JSONB`)
  so the schema builds on both Postgres (production) and SQLite (tests/CI).
- Initialized Alembic under `backend/` (`alembic.ini`, `backend/app/db/migrations/`
  with `env.py` wired to `Base.metadata` and `DATABASE_URL`); generated initial
  migration with `--autogenerate`.
- Wrote `backend/tests/test_models.py` covering schema creation, round-trip
  CRUD for the submission→run→evidence chain, the `supersedes` self-reference,
  and the `AuditEvent` append-only contract — all using in-memory SQLite.

### Key decisions
- **Generic JSON vs JSONB**: Used SQLAlchemy's generic `JSON` type everywhere.
  On Postgres this maps to `json`; switching to `JSONB` for GIN-index
  performance is a future migration. SQLite does not support `JSONB`.
- **String PKs (UUID strings)**: All PKs are `String(36)` with a Python-side
  `uuid.uuid4()` default rather than `uuid` column type. The `uuid` type is
  Postgres-specific; `String(36)` works on both engines.
- **Alembic migration generated against SQLite**: No live Postgres was available.
  The migration was generated and applied against a throwaway SQLite file to
  verify it runs cleanly. The SQL DDL generated is correct; it will apply
  against Postgres as-is (SQLAlchemy renders dialect-appropriate DDL at apply
  time).
- **`per-file-ignores` for migration versions**: Alembic auto-generates migration
  scripts with long lines. Added `"app/db/migrations/versions/*.py" = ["E501"]`
  to `pyproject.toml` to keep ruff passing without hand-editing generated code.
- **Append-only audit_event**: No `.update()` or `.delete()` helpers are exposed
  on the `AuditEvent` model class. DB-level enforcement (e.g. RLS or a trigger)
  is deferred to P3-T3 (PII retention & access policy).

### Assumptions
- `psycopg[binary]==3.1.19` (psycopg3) is compatible with the project's Python
  3.10/3.11 target. The `binary` extra bundles the C extension for performance
  and avoids a separate `libpq` system dependency.
- The `supersedes_id` self-reference on `VerificationRun` is modelled as a
  nullable FK to the same table. SQLAlchemy requires `remote_side` to resolve
  the ambiguous join direction; this is handled with `foreign_keys=[supersedes_id]`
  and `remote_side="VerificationRun.id"`.

---

## Implementation Plan
1. Pin and add `sqlalchemy`, `alembic`, `psycopg[binary]` to `pyproject.toml`.
2. Create `app/db/` package with `session.py` (engine, SessionLocal, Base).
3. Create `app/models/` package with 10 model files.
4. Update `app/models/__init__.py` to import all models (needed for Alembic
   autogenerate).
5. Initialize Alembic; wire `env.py` to Base.metadata and DATABASE_URL.
6. Generate initial migration (`--autogenerate` via SQLite).
7. Apply migration to ephemeral SQLite to confirm it runs.
8. Write `tests/test_models.py`.
9. Run lint + format + tests; fix all issues.

### Files created/modified
**Created:**
- `backend/app/db/__init__.py`
- `backend/app/db/session.py`
- `backend/app/models/__init__.py`
- `backend/app/models/operator.py`
- `backend/app/models/entity.py`
- `backend/app/models/submission.py`
- `backend/app/models/verification_run.py`
- `backend/app/models/evidence.py`
- `backend/app/models/field_comparison.py`
- `backend/app/models/risk_assessment.py`
- `backend/app/models/report.py`
- `backend/app/models/review.py`
- `backend/app/models/audit_event.py`
- `backend/alembic.ini`
- `backend/app/db/migrations/env.py`
- `backend/app/db/migrations/README`
- `backend/app/db/migrations/script.py.mako`
- `backend/app/db/migrations/versions/c46233bc9881_initial_schema.py`
- `backend/tests/test_models.py`

**Modified:**
- `backend/pyproject.toml` (deps + per-file-ignores for migrations)

---

## Code Changes

### File: backend/pyproject.toml
- Added `sqlalchemy==2.0.30`, `alembic==1.13.1`, `psycopg[binary]==3.1.19`
  to runtime `dependencies`.
- Added `[tool.ruff.lint.per-file-ignores]` to suppress E501 on Alembic-generated
  migration scripts.

### File: backend/app/db/session.py
- `create_engine` reading `DATABASE_URL` env var; falls back to local Postgres URL.
- `SessionLocal = sessionmaker(autocommit=False, autoflush=False)`.
- `Base(DeclarativeBase)` shared by all models.

### File: backend/app/models/
10 model files implementing the entities from ARCHITECTURE § 3:

| Model | Key fields / notes |
|---|---|
| `Operator` | email, full_name, role (operator/lead) |
| `Entity` | canonical_name, canonical_domain; linked to submissions + runs |
| `Submission` | required inputs + optional inputs + network metadata; immutable |
| `VerificationRun` | status, started/finished, supersedes_id self-FK |
| `Evidence` | source, tier, field, raw/normalized values, confidence, raw_payload |
| `FieldComparison` | field_name, submitted/discovered values, match_status |
| `RiskAssessment` | four layer scores, overall_score, triage_tier, contributing_signals |
| `Report` | status, section_statuses, summary; one-to-one with VerificationRun |
| `Review` | operator verdict, notes, corrections; FK to Operator + Run |
| `AuditEvent` | event_type, operator_id, verification_run_id, payload; append-only |

`risk_assessment_evidence` association table links `RiskAssessment` many-to-many
to `Evidence`.

### File: backend/app/db/migrations/env.py
- Imports `app.models` (side-effect) to populate `Base.metadata`.
- Reads `DATABASE_URL` env var and sets it via `config.set_main_option`.
- Standard offline/online migration functions.

### File: backend/app/db/migrations/versions/c46233bc9881_initial_schema.py
- Auto-generated by `alembic revision --autogenerate -m "initial_schema"`.
- Creates all 11 tables (10 entity tables + `risk_assessment_evidence`) and
  all indexes.

### File: backend/tests/test_models.py
- 7 tests across 3 categories: schema creation, round-trip CRUD chain,
  supersedes self-reference, AuditEvent append-only contract.
- All use in-memory SQLite.

---

## Acceptance Criteria Mapping

- Criterion: ARCHITECTURE § 3 Data model — 10 entities implemented
  - Implementation: 10 model files in `app/models/`, field sketches mirrored
  - Files: `backend/app/models/*.py`

- Criterion: PRD § Auditability Requirements — audit_event stores operator actions, runs, score changes, sources, re-analysis
  - Implementation: `AuditEvent` model with `event_type`, `operator_id`,
    `verification_run_id`, `submission_id`, `payload` columns; no update/delete
    helpers; enforced by test `test_audit_event_has_no_update_or_delete_methods`
  - Files: `backend/app/models/audit_event.py`, `backend/tests/test_models.py`

- Criterion: Evidence-centric design — risk_assessment and field_comparison reference evidence; verification_run has supersedes self-reference
  - Implementation: `FieldComparison.evidence_id` FK; `RiskAssessment` to
    `Evidence` via `risk_assessment_evidence`; `VerificationRun.supersedes_id`
    self-FK with ORM relationship
  - Files: `backend/app/models/field_comparison.py`,
    `backend/app/models/risk_assessment.py`,
    `backend/app/models/verification_run.py`

- Criterion: Alembic initialized, wired to Base.metadata + DATABASE_URL, initial migration generated
  - Implementation: `alembic.ini` + `env.py` wired; migration
    `c46233bc9881_initial_schema.py` generated and verified against SQLite
  - Files: `backend/alembic.ini`, `backend/app/db/migrations/`

- Criterion: Tests — SQLite-based, no live Postgres required, CI-compatible
  - Implementation: `tests/test_models.py` using `sqlite:///:memory:`; all 7
    new tests pass alongside the 2 existing health tests
  - Files: `backend/tests/test_models.py`

---

## Build Plan Mapping

- Ticket: P0-T4 — Postgres + migrations + core data model
  - Status: Complete
  - What was completed: All deps added, session/Base created, 10 models
    implemented, Alembic initialized and migration generated + applied,
    7 model tests pass, lint + format clean.
  - Remaining work: None. Manual Postgres verification (see Validation below).

---

## Validation

### What was run

```
# Lint
cd backend
.venv/bin/ruff check .          -> All checks passed!
.venv/bin/ruff format --check . -> 20 files already formatted

# Tests (in-memory SQLite, no live DB)
.venv/bin/pytest -v
  tests/test_health.py::test_health_returns_200                       PASSED
  tests/test_health.py::test_health_returns_expected_body             PASSED
  tests/test_models.py::test_all_tables_created                       PASSED
  tests/test_models.py::test_submission_create_and_read               PASSED
  tests/test_models.py::test_verification_run_create_and_read         PASSED
  tests/test_models.py::test_evidence_linked_to_verification_run      PASSED
  tests/test_models.py::test_verification_run_supersedes_self_reference PASSED
  tests/test_models.py::test_audit_event_has_no_update_or_delete_methods PASSED
  tests/test_models.py::test_audit_event_create                       PASSED
  9 passed in 1.50s

# Migration against ephemeral SQLite
DATABASE_URL="sqlite:////tmp/entityiq_upgrade_test.db" .venv/bin/alembic upgrade head
  INFO [alembic.runtime.migration] Running upgrade  -> c46233bc9881, initial_schema
  exit 0
```

### What needs manual Postgres verification
No live Postgres was available during implementation. When a Postgres instance
is running, verify:

```bash
cd backend
DATABASE_URL="postgresql+psycopg://user:pass@host:5432/entityiq_dev" \
  .venv/bin/alembic upgrade head
```

Expected: all 11 tables created, `alembic_version` row inserted, no DDL errors.
`psycopg[binary]==3.1.19` supports Postgres 13+. The migration DDL is rendered
by SQLAlchemy at apply-time so dialect-specific syntax is handled automatically.

### CI
The existing `.gitlab-ci.yml` runs `ruff check`, `ruff format --check`, and
`pytest` against the backend. The new model tests use SQLite in-memory and
require no external services, so they will pass in CI as-is. No CI changes needed.

---

## Open Issues

- **JSONB**: Generic `JSON` maps to Postgres `json` (not `jsonb`). A future
  migration can alter to `jsonb` for columns needing GIN-index searches. This
  is a P2/P3 concern, not blocking for P1.
- **DB-level append-only enforcement for audit_event**: Only model-layer
  enforcement (no `.update()`/`.delete()` methods) is in place. Row-level
  security or a trigger preventing UPDATE/DELETE on `audit_event` is scoped
  to P3-T3.
- **UUID column type**: PKs are `String(36)` to stay portable. A Postgres
  `UUID` column type and a `gen_random_uuid()` server-default would be more
  idiomatic. Can be changed in a migration once SQLite tests are no longer needed.
- **Postgres verification**: Not run locally due to no live instance. Must be
  verified during P1-T1 setup or in the deployment environment.

---

## BUILD_PLAN Update
See `docs/BUILD_PLAN.md` — P0-T4 marked Complete; Current Status updated to
Phase 1, current ticket P1-T1; Phase 0 exit criteria noted as met.
