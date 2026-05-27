# Implementation

## Scope Implemented
- Requested scope: P2-T6, P2-T7
- Related phase: Phase 2 — Deepen the Tracks
- Related ticket(s): P2-T6 (Full four-layer scoring + signal catalog), P2-T7 (Explainability + triage tiers)

## Approach

### P2-T6
- `signals.py` contains the full PRD signal catalog for all four scoring layers, called lazily from `engine.py` to avoid circular imports.
- `engine.py` updated to delegate each layer function to `signals.py` and to accept optional `run_id`+`db` parameters for cross-submission IP/ASN reuse detection (deferred from P2-T2).
- `_cross_submission_reuse_signals()` in `signals.py` queries Evidence rows from other runs sharing the same ASN — 1 hit → `ip_asn_reuse` (w=0.3); ≥3 hits → `ip_asn_reuse_high` (w=0.6).
- Absence signals (`no_registry_evidence`, etc.) have `evidence_ids=[]` by design — they document missing sources, not findings.
- Scoring is advisory only: `ScoringResult` has no `decision`/`approved`/`rejected` attribute.

### P2-T7
- `triage.py` derives `TriageResult` (tier, reason, critical_signals, advisory_note) from `ScoringResult`. Two paths: (1) critical-signal override (sanctions/ASN-reuse-high → always escalate); (2) score-threshold path.
- `explain.py` builds `ExplainabilityPayload` with per-layer `LayerExplanation` (score + signals + sources), `MismatchDetail` list, and `source_coverage` map.
- `report.py` extended: `_build_summary()` adds `triage` and `explainability` top-level keys to the report summary JSON, reconstructing Signal objects from persisted `RiskAssessment.contributing_signals` without re-running the engine.

### Key decisions
- Lazy imports in `engine.py` layer helpers prevent the `signals.py` → `engine.Signal` → `signals.py` circular import.
- `report.py` derives triage/explainability from the persisted `RiskAssessment` data rather than re-scoring, keeping `StoreReportStage` idempotent.
- `TriageResult.advisory_note` is always set on every result; tests assert its presence.
- No new endpoints or UI changes — downstream FE/API reads the new fields from the existing `GET /reports/{run_id}` summary.

---

## Implementation Plan

### P2-T6 (committed first)
1. Add `backend/app/scoring/signals.py` — full PRD signal catalog
2. Modify `backend/app/scoring/engine.py` — delegate to signals.py, add run_id/db params
3. Add `backend/tests/scoring/test_signals.py` — 30 new tests
4. Update docs, commit

### P2-T7
1. Add `backend/app/scoring/triage.py` — TriageResult + derive_triage()
2. Add `backend/app/scoring/explain.py` — ExplainabilityPayload + build_explainability()
3. Modify `backend/app/scoring/report.py` — include triage + explainability in summary
4. Add `backend/tests/scoring/test_triage.py` — 24 new tests
5. Update docs, commit

---

## Code Changes

### File: `backend/app/scoring/signals.py` (new)
- Full PRD signal catalog for all four layers.
- `entity_legitimacy_signals()`, `infrastructure_legitimacy_signals()`, `representation_confidence_signals()`, `fraud_staging_risk_signals()`.
- `_cross_submission_reuse_signals()` for cross-submission IP/ASN reuse detection.

### File: `backend/app/scoring/engine.py` (modified)
- Docstring updated to document P2-T6 changes.
- Layer helper functions now delegate to `signals.py` via lazy imports.
- `ScoringEngine.score()` accepts optional `run_id` + `db` parameters.

### File: `backend/app/scoring/triage.py` (new)
- `TriageResult` dataclass: tier, reason, critical_signals, advisory_note.
- `derive_triage(result: ScoringResult) -> TriageResult`.
- Critical signal set: `sanctions_hit`, `sanctions_hit_fraud_flag`, `ip_asn_reuse_high`.

### File: `backend/app/scoring/explain.py` (new)
- `ExplainabilityPayload`, `LayerExplanation`, `SignalDetail`, `MismatchDetail` dataclasses.
- `build_explainability(result, evidence_rows, field_comparisons) -> ExplainabilityPayload`.

### File: `backend/app/scoring/report.py` (modified)
- `_build_summary()` adds `triage` and `explainability` keys to the summary dict.
- `_derive_triage_from_assessment()` and `_build_explainability_from_assessment()` reconstruct from persisted JSON.

### File: `backend/tests/scoring/test_signals.py` (new, P2-T6)
- 30 tests: clean entity, sanctions hit, registry mismatch, fraud stack, confidence, IP/ASN reuse, IP country mismatch, trust catalog, no-orphan integration.

### File: `backend/tests/scoring/test_triage.py` (new, P2-T7)
- 24 tests: pre_clear with full explanation, sanctions escalate, mismatch escalate, no auto-approval invariant, explainability payload integrity, report summary inclusion.

---

## Acceptance Criteria Mapping

- PRD § Core Verification Philosophy (four layers): All four layer scores computed with full signal catalog.
- PRD § Risk Signals (elevated + trust): Full catalog in `signals.py` covering all PRD-listed signals.
- PRD § Risk Scoring (overall 0-100 + confidence breakdown): `ScoringEngine` produces overall + four layer scores + confidence.
- PRD § Explainability Requirements (evidence, source attribution, contributing signals, mismatches): `ExplainabilityPayload` surfaces all four.
- PRD § Non-Goals (no auto-approval): `TriageResult` has no approval state; advisory_note always present; tests assert invariant.
- STRATEGY § Our approach (triage): `pre_clear`/`review`/`escalate` tiers feed the operator queue.
- ARCHITECTURE § 3 (`risk_assessment` + `risk_assessment_evidence`): All signal evidence_ids reference valid evidence rows; no orphan signals.

---

## Build Plan Mapping

- Ticket: P2-T6
  - Status: Complete (2026-05-27)
  - What was completed: signals.py with full PRD catalog; engine.py delegates + cross-submission reuse; test_signals.py; 332 tests pass.
  - Remaining: none

- Ticket: P2-T7
  - Status: Complete (2026-05-27)
  - What was completed: triage.py + explain.py; report.py updated; test_triage.py; 356 tests pass.
  - Remaining: none

---

## Validation

- `backend/.venv/bin/ruff check .` → All checks passed!
- `backend/.venv/bin/ruff format --check .` → 81 files already formatted
- `backend/.venv/bin/pytest -q` → 356 passed in 7.75s

---

## Open Issues

- IPINFO_TOKEN (free tier in use; paid plan unresolved — same class as Open Decision #5).
- OpenCorporates production licensing (Open Decision #5) still unresolved; adapter has warning in docstring.
- No new API endpoints expose triage/explainability directly — downstream FE (P2-T8) and API (P2-T11) consume from `report.summary`.

---

## BUILD_PLAN Update

- Current phase: Phase 2 — Deepen the Tracks
- Current ticket: P2-T8 — Detail view completeness
- P2-T6: Complete (2026-05-27)
- P2-T7: Complete (2026-05-27)
- Blockers: Open Decision #5, IPINFO_TOKEN (both pre-existing; unblocked for P2-T8)
- Recommended next ticket: P2-T8 — Detail view completeness
