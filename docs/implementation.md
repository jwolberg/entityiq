# Implementation

## Scope Implemented
- Requested scope: P1-T7, P1-T8, P1-T9
- Related phase: Phase 1 — MVP Vertical Slice (walking skeleton)
- Related ticket(s): P1-T7, P1-T8, P1-T9

## Approach
- Three tickets implemented in order, each as an atomic commit.
- Deterministic v1 scoring engine: four layer scores + overall score, advisory-only
  (no approve/reject decision). Missing sources → reduced confidence, NOT low risk.
- Report assembly is idempotent: `assemble_report()` create-or-updates the Report row.
- Per-section statuses derived from `VerificationRun.source_availability` for partial
  result readability.
- Operator auth uses stdlib-only PBKDF2-HMAC-SHA256 (no new dependencies).
- In-memory session store (MVP single-process); designed behind an interface for
  OIDC/SSO replacement without touching route code.
- Security tests (unauthorized/forbidden) written first per P1-T9 spec.
- Audit log is append-only at both model and recorder layers.

### Key decisions
1. `ScoringResult` has no `decision`/`approved`/`rejected` field — enforced by
   invariant. Advisory-only `triage_tier` values are only pre_clear/review/escalate.
2. Missing evidence → baseline scores of 60 (entity) and 40 (infra), both above
   TRIAGE_PRE_CLEAR_MAX=30 — missing data cannot yield a pre_clear tier.
3. `record_event()` calls `db.commit()` internally for audit durability — even if
   the caller raises after the call, the audit row persists.
4. Each router has its own `_get_db` dependency so tests can override independently
   (auth_get_db, reviews_get_db, reports._get_db).
5. Password hashing uses 260,000 PBKDF2 iterations (NIST SP 800-132 recommendation).
6. `_clear_all_sessions()` exposed as test-only helper; called in `auth_client` fixture
   teardown to prevent token leakage across tests.

### Assumptions
- `ruff check` / `ruff format --check` / `pytest -q` are the validation commands.
- No new third-party dependencies introduced for P1-T7, P1-T8, or P1-T9.

---

## Implementation Plan (executed)

### P1-T7
1. Create `backend/app/scoring/__init__.py`
2. Create `backend/app/scoring/engine.py` — Signal, ScoringResult, ScoringEngine,
   ScoringStage, TRIAGE_PRE_CLEAR_MAX, TRIAGE_ESCALATE_MIN
3. Create `backend/app/scoring/report.py` — assemble_report(), StoreReportStage,
   _build_section_statuses, _build_summary
4. Update `backend/app/pipeline/orchestrator.py` to register ScoringStage +
   StoreReportStage in default_stages()
5. Create `backend/tests/scoring/__init__.py`,
   `backend/tests/scoring/test_engine.py` (16 tests)
6. Append P1-T7 entry to `docs/implementation-notes.md`
7. Update `docs/BUILD_PLAN.md`
8. Lint + test → commit

### P1-T8
1. Create `backend/app/schemas/report.py` — Pydantic v2 schemas
2. Create `backend/app/api/reports.py` — GET /{run_id} endpoint + _get_db
3. Update `backend/app/main.py` to include reports_router
4. Create `backend/tests/api/__init__.py`,
   `backend/tests/api/test_reports.py` (4 tests)
5. Append P1-T8 entry to `docs/implementation-notes.md`
6. Update `docs/BUILD_PLAN.md`
7. Lint + test → commit

### P1-T9
1. Add `password_hash` column to `backend/app/models/operator.py`
2. Create `backend/app/auth/operator.py` — hash_password, verify_password,
   session store, sign_in, get_current_operator, require_lead, auth_router
3. Create `backend/app/audit/recorder.py` — record_event (append-only)
4. Create `backend/app/api/reviews.py` — POST /{run_id} (requires auth)
5. Update `backend/app/main.py` to include auth_router + reviews_router
6. Create `backend/tests/auth/__init__.py`,
   `backend/tests/auth/test_operator.py` (17 tests — security tests first)
7. Create `backend/tests/audit/__init__.py`,
   `backend/tests/audit/test_recorder.py` (10 tests)
8. Append P1-T9 entry to `docs/implementation-notes.md`
9. Update `docs/BUILD_PLAN.md`
10. Lint + test → commit

---

## Code Changes

### File: backend/app/scoring/__init__.py
- Change summary: New package init (empty).

### File: backend/app/scoring/engine.py
- Change summary: Core scoring engine. Constants TRIAGE_PRE_CLEAR_MAX=30,
  TRIAGE_ESCALATE_MIN=70. Signal dataclass (signal_type, value, confidence,
  evidence_ids, description). ScoringResult dataclass (overall_score, triage_tier,
  entity_legitimacy, infrastructure_legitimacy, representation_confidence,
  fraud_staging_risk, signals). ScoringEngine.score() computes four layer scores
  and overall weighted average. ScoringStage pipeline wrapper. Advisory-only:
  no decision field.

### File: backend/app/scoring/report.py
- Change summary: assemble_report(run_id, db) creates or updates Report row with
  section_statuses and summary JSON. _build_section_statuses derives per-section
  status from VerificationRun.source_availability. _build_summary builds
  denormalized JSON with scores, evidence, mismatches, sources. StoreReportStage
  pipeline wrapper (name="store_report").

### File: backend/app/pipeline/orchestrator.py
- Change summary: default_stages() now returns 6 stages in order:
  NormalizeInputStage → QueryRegistriesStage → AnalyzeDomainStage →
  ConsistencyChecksStage → ScoringStage → StoreReportStage.

### File: backend/app/schemas/report.py
- Change summary: Pydantic v2 schemas: ScoresSchema, EvidenceItemSchema,
  MismatchItemSchema, SourceSummarySchema, SectionStatuses, ReportResponse.

### File: backend/app/api/reports.py
- Change summary: GET /{run_id} endpoint. Checks VerificationRun exists (404),
  then Report exists (404 "not been assembled yet"), returns serialized ReportResponse.
  Own _get_db dependency for independent test override.

### File: backend/app/models/operator.py
- Change summary: Added password_hash: Mapped[str | None] = mapped_column(Text,
  nullable=True). Added Text to SQLAlchemy imports.

### File: backend/app/auth/operator.py
- Change summary: Full operator auth module. hash_password / verify_password
  (PBKDF2-HMAC-SHA256, 260k iterations, stdlib only). _session_store dict + helpers.
  sign_in() validates credentials, returns token or None. get_current_operator()
  FastAPI dep (401 on failure). require_lead() FastAPI dep (403 if not lead role).
  SignInRequest / SignInResponse Pydantic models. auth_router POST /auth/sign-in
  with audit event recording.

### File: backend/app/audit/recorder.py
- Change summary: record_event(db, event_type, *, operator_id, verification_run_id,
  submission_id, payload, description) -> str. Inserts AuditEvent, db.commit(),
  returns event.id. No update/delete helpers at module level.

### File: backend/app/api/reviews.py
- Change summary: POST /{run_id} endpoint. Requires get_current_operator dep (401
  if unauthenticated). Checks run exists (404). Checks no existing review (409).
  Creates Review row. Calls record_event("operator.mark_reviewed", ...).

### File: backend/app/main.py
- Change summary: Added include_router calls for reports_router, auth_router,
  reviews_router.

### File: backend/tests/scoring/test_engine.py
- Change summary: 16 tests. TestLowRiskScenario, TestElevatedRiskScenario,
  TestMissingSourcesScenario, TestIntegrationNoOrphanSignals, TestNoAutoApproval,
  TestStoreReport.

### File: backend/tests/api/test_reports.py
- Change summary: 4 tests. Full report for completed run, partial for in-progress
  run, 404 for unknown run_id, 404 for run with no report yet.

### File: backend/tests/auth/test_operator.py
- Change summary: 17 tests. Security tests first: TestUnauthenticated (401 no-token,
  bad-token, malformed), TestRBAC (403 operator on lead route, lead passes).
  TestSignIn, TestPasswordHashing (5 pure unit), TestSignInUnit (3 direct tests).

### File: backend/tests/audit/test_recorder.py
- Change summary: 10 tests. TestAuditImmutability (no update/delete on model or
  module), TestRecordEvent (persist, correct fields, append-only, system events),
  TestMarkReviewedAudit (attribution, occurred_at).

---

## Acceptance Criteria Mapping

- **Four layer scores + overall score**: entity_legitimacy, infrastructure_legitimacy,
  representation_confidence, fraud_staging_risk all present in ScoringResult — satisfied.
- **Signals reference evidence rows via risk_assessment_evidence**: Signal.evidence_ids
  populated from persisted Evidence row IDs; integration test verifies no orphans — satisfied.
- **Advisory only — NO approve/reject decision**: ScoringResult has no decision field;
  triage_tier values are only pre_clear/review/escalate — verified by TestNoAutoApproval.
- **Missing sources → reduced confidence, not low risk**: baseline scores above 30 when
  no evidence → cannot yield pre_clear tier — verified by TestMissingSourcesScenario.
- **Per-section status so partial results are readable**: _build_section_statuses derives
  pending/complete/unavailable from source_availability — verified in test_reports.py.
- **GET /reports/{run_id}**: returns ReportResponse with section_statuses + summary JSON
  — verified by 4 API tests.
- **Operator sign-in → scoped session token**: POST /auth/sign-in returns session_token,
  operator_id, role — verified by TestSignIn.
- **Unauthorized requests → 401**: no token, bad token, malformed header all return 401
  — verified by TestUnauthenticated.
- **Operator attempting lead-only action → 403**: require_lead() raises HTTP 403 for
  operator role — verified by TestRBAC.
- **Audit events are append-only**: AuditEvent has no update/delete methods; recorder has
  no update_event/delete_event — verified by TestAuditImmutability.
- **mark-reviewed writes attributable audit_event**: POST /reviews/{run_id} calls
  record_event with operator_id — verified by TestMarkReviewedAudit.

---

## Build Plan Mapping

- P1-T7: Complete (2026-05-26). Deterministic v1 scoring engine + report assembly.
- P1-T8: Complete (2026-05-26). Report retrieval endpoint with per-section statuses.
- P1-T9: Complete (2026-05-26). Operator auth + RBAC + append-only audit foundation.

---

## Validation

### P1-T7
- `ruff check .` → All checks passed
- `ruff format --check .` → All files already formatted
- `pytest -q` → 137 passed (16 new scoring tests)

### P1-T8
- `ruff check .` → All checks passed
- `ruff format --check .` → All files already formatted
- `pytest -q` → 141 passed (4 new API tests)

### P1-T9
- `ruff check .` → All checks passed
- `ruff format --check .` → All files already formatted
- `pytest -q` → 168 passed (27 new auth/audit tests)

All tests run offline (SQLite StaticPool + in-memory session store). No live Redis,
Postgres, or network required.

---

## Open Issues

- **In-memory session store is single-process only**: _session_store dict is not
  shared across processes. Replacement path documented in module docstring: swap for
  Redis-backed store or JWT validation. Acceptable for MVP.
- **PBKDF2 is suitable for MVP; upgrade path noted**: Module docstring notes argon2/
  bcrypt as P3 upgrade. No action required now.
- **Open Decision #5 (OpenCorporates licensing)** — UNRESOLVED from P1-T4. Inherited.
- **Domain adapter optional deps (python-whois, dnspython)** not in pyproject.toml.
  Deferred from P1-T5.

---

## BUILD_PLAN Update

- P1-T7: Complete (2026-05-26)
- P1-T8: Complete (2026-05-26)
- P1-T9: Complete (2026-05-26)
- Current ticket updated to: P1-T10 — Operator app: list + detail + mark reviewed
- Recommended next: P1-T10 (depends on P1-T8 and P1-T9, both now complete)
