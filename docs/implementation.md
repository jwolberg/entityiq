# Implementation

## Scope Implemented
- Requested scope: Service-credential (API key) authentication at the API boundary
  for integrating systems, with per-system attribution on submissions and audited
  actions.
- Related phase: Phase 2 — Deepen the Tracks (Track: Integration & reporting API)
- Related ticket(s): **P2-T12 — API auth for integrating systems**

## Approach
- High-level strategy: **Backend-only.** Add an `ApiClient` service-credential
  table, an API-key auth module, and apply it at the two endpoints integrating
  systems use — `POST /submissions` (ingest) and `GET /reports/{id}/export`
  (machine-readable pull) — recording per-system attribution in the audit trail.
- Key decisions:
  - **API keys via the `X-API-Key` header**, kept distinct from operator
    `Authorization: Bearer` session tokens so the two auth schemes never collide
    (ARCHITECTURE § 5: "API keys to start").
  - Keys are **never stored in plaintext**: only a PBKDF2-HMAC-SHA256 hash (reusing
    the operator password primitive — no new dependency) plus a non-secret 12-char
    `key_prefix` used to look the row up before a constant-time full-key verify.
  - **Submission requires a valid service credential** (401 otherwise) and stamps
    `submission.api_client_id`; a `system.submission_received` audit event is
    attributed to the calling system.
  - **Report export requires a `Principal`** — either an integrating system
    (`X-API-Key`) or an operator (`Bearer`). This keeps the operator UI's export
    button working (it already sends the operator token) while making machine pulls
    authenticated + attributed (a `report.exported` audit event records who pulled).
  - **Scope boundary:** only the two integration endpoints are tightened. The
    operator-UI report endpoints (`GET /reports/`, `GET /reports/{id}`) are left
    open exactly as before — locking the full operator surface (OIDC/session
    enforcement on every read) is a separate Phase-3 hardening concern, not this
    ticket. Noted as a follow-up.
  - **Attribution columns** (`api_client_id`) added to `submission` and
    `audit_event`; the audit recorder gained an `api_client_id` parameter.
  - Provisioning is via `create_api_client(name, db)` (returns the plaintext once);
    a provisioning UI/endpoint is out of scope.
- Assumptions:
  - Single-process MVP: same posture as the existing operator session store.
  - SQLite remains the verification DB; the migration uses Alembic **batch mode** so
    the new FK columns apply on SQLite (copy-and-move) as well as Postgres.

---

## Implementation Plan
1. `models/api_client.py` (new) — `ApiClient`; register in `models/__init__.py`.
2. Add nullable `api_client_id` FK to `models/submission.py` + `models/audit_event.py`.
3. `auth/service.py` (new) — key gen/hash/verify, `create_api_client`,
   `get_current_api_client` (X-API-Key required), `Principal` + `get_principal`.
4. `audit/recorder.py` — add `api_client_id` attribution.
5. `api/submissions.py` — require `get_current_api_client`; attribute + audit ingest.
6. `api/reports.py` `/export` — require `get_principal`; audit attributed pulls.
7. Alembic migration (batch mode) for the table + columns.
8. `tests/conftest.py` — seed a credential, override `service._get_db`, default
   `X-API-Key` header; update `test_workflow_actions.py` export tests to authenticate;
   new `tests/auth/test_service.py`.

---

## Code Changes

### File: backend/app/models/api_client.py  (new)
- `ApiClient` table: id, unique `name`, unique+indexed `key_prefix`, `key_hash`,
  `active`, `created_at`, `last_used_at`.

### File: backend/app/models/__init__.py
- Registered `ApiClient` in the ORM registry / `__all__`.

### File: backend/app/models/submission.py · backend/app/models/audit_event.py
- Added nullable `api_client_id` FK → `api_client.id` (indexed) for per-system
  attribution.

### File: backend/app/auth/service.py  (new)
- `generate_api_key` / `hash_api_key` / `verify_api_key` (reuse PBKDF2 primitive);
  `create_api_client`; `_resolve_api_client` (prefix lookup + constant-time verify +
  `last_used_at` touch); `get_current_api_client` (X-API-Key required, 401 on
  missing/invalid/revoked); `Principal` dataclass + `get_principal` (system via
  X-API-Key OR operator via Bearer; 401 if neither).

### File: backend/app/audit/recorder.py
- `record_event` gained an `api_client_id` parameter, persisted on the AuditEvent.

### File: backend/app/api/submissions.py
- `submit()` now depends on `get_current_api_client`; stamps `api_client_id` on the
  submission and records a `system.submission_received` audit event attributed to
  the calling system.

### File: backend/app/api/reports.py
- `export_report()` now depends on `get_principal`; records a `report.exported`
  audit event attributed to the operator or system that pulled it.

### File: backend/app/db/migrations/versions/a1b2c3d4e5f6_*.py  (new)
- Creates `api_client`; adds `api_client_id` (+ index + FK) to `submission` and
  `audit_event` via batch mode (SQLite + Postgres compatible).

### Files: backend/tests/conftest.py, tests/api/test_workflow_actions.py,
### tests/auth/test_service.py (new)
- conftest: session-scoped `service_credential`, override `service._get_db`, default
  `X-API-Key` header so existing submission tests authenticate.
- workflow export tests: override `service._get_db`, authenticate the two export
  calls with an operator token.
- new test_service: hashing primitives, submission 401 (missing/invalid/revoked),
  attribution (FK + audit), export 401 + attributed audit.

---

## Acceptance Criteria Mapping
- Criterion (ARCHITECTURE § 5): **Integrating systems authenticate at the API
  boundary with service credentials.**
  - Implementation: `X-API-Key` required on `POST /submissions`; `get_principal`
    (system or operator) on `GET /reports/{id}/export`.
  - File(s): `auth/service.py`, `api/submissions.py`, `api/reports.py`
- Criterion (ARCHITECTURE § 5; USERS § 2): **Submissions and report pulls are
  attributable to the calling system for audit.**
  - Implementation: `submission.api_client_id`; `system.submission_received` and
    `report.exported` audit events carry `api_client_id`.
  - File(s): `models/submission.py`, `models/audit_event.py`, `audit/recorder.py`,
    `api/submissions.py`, `api/reports.py`

---

## Build Plan Mapping
- Ticket: **P2-T12 — API auth for integrating systems**
  - Status: **Complete**
  - What was completed: API-key auth at the boundary (submission + export), keys
    hashed at rest, per-system attribution on submissions and in the audit trail.
    Lint + 383 tests pass; migration verified up/down/up on SQLite.
  - Remaining work: None for this ticket. mTLS, key-provisioning UI/endpoint, and
    locking down the operator-only report reads are deliberate follow-ups.

---

## Validation
- How the feature was tested: `tests/auth/test_service.py` (8 tests) covers the
  hashing primitives, submission 401 for missing/invalid/revoked keys, successful
  attribution (submission FK + `system.submission_received` audit), export 401
  without a principal, and an attributed `report.exported` audit on API-key export.
  Existing submission + export tests were updated to authenticate.
- Lint/test results:
  - `ruff check app tests` → All checks passed.
  - `ruff format --check` → all files formatted.
  - `pytest` → **383 passed**.
  - `alembic upgrade head` / `downgrade -1` / `upgrade head` on SQLite → clean.
- Manual verification steps: provision a key via `create_api_client("name", db)`;
  `POST /submissions` with `X-API-Key: <key>` → 202 (without it → 401); the
  Submission row and a `system.submission_received` audit event carry the
  `api_client_id`. `GET /reports/{id}/export` with the key (or an operator Bearer
  token) → 200 + a `report.exported` audit event.
- Visible user outcome: Integrating systems now authenticate at the API boundary,
  and every machine submission/report-pull is attributable to a named system in the
  audit trail. The operator UI is unaffected (its export uses the operator token).

---

## Open Issues
- Known limitations:
  - In-memory single-process posture (same as operator sessions); Redis/JWT and
    mTLS are future work.
  - No provisioning UI/endpoint yet — keys are minted via `create_api_client`.
  - Operator-only report reads (`GET /reports/`, `/reports/{id}`) remain open as
    before; full read-surface auth is Phase-3 hardening.
- Unresolved edge cases: None observed.
- Blockers: None for P2-T12. (Project blockers unchanged: Open Decision #5 Tier-1
  licensing; IPINFO_TOKEN production plan; #4 blocks P2-T9; #7 blocks P3-T3.)

---

## BUILD_PLAN Update
- Current phase: Phase 2 — Deepen the Tracks (complete except P2-T9, which is
  blocked on Open Decision #4)
- Current ticket: P2-T12 — Complete
- Updated ticket status: P2-T12 → Complete
- Blockers: unchanged (#5 licensing, IPINFO_TOKEN; #4 blocks P2-T9, #7 blocks P3-T3)
- Recommended next ticket: **P3-T1 — Performance & partial-result UX** (start of
  Phase 3). P2-T9 (HQ visualization) stays blocked on Open Decision #4; P3-T2
  (auditability completeness + lead audit views) is a natural follow-on to this
  ticket's attribution work.
