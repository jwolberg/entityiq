# Implementation

## Scope Implemented
- Requested scope: P1-T4, P1-T5, P1-T6
- Related phase: Phase 1 — MVP Vertical Slice (walking skeleton)
- Related ticket(s): P1-T4, P1-T5, P1-T6

## Approach
- Three tickets implemented in order, each as an atomic commit.
- Adapter contract defined first (base.py) so both adapters share the same
  typed-failure vocabulary (AdapterSuccess / AdapterFailure).
- HTTP/WHOIS/DNS/SSL clients injected via constructor parameters; all tests
  use in-process fakes — zero network calls.
- Placeholder stubs for domain.py and consistency.py created during P1-T4 so
  that `orchestrator.default_stages()` imports all four stage classes without error.
  Stubs replaced by full implementations in P1-T5 and P1-T6 respectively.
- Match logic in consistency.py uses loose (substring / token-overlap) semantics to
  handle real-world registry formatting differences.

### Key decisions
1. Injectable client pattern (not monkeypatching) for testability — matches the
   existing pattern used for `enqueue_run` in the orchestrator tests.
2. `AdapterResult = AdapterSuccess | AdapterFailure` union avoids exceptions
   propagating out of the pipeline; the stage wrapper checks `isinstance`.
3. `recently_registered` threshold is 180 days (constant; matches PRD § Risk Signals).
4. Consistency checks use three fields (company_name, country_iso, billing_address)
   against evidence fields (company_name, jurisdiction, legal_address). Additional
   fields deferred to P1-T7 / P2-T6.
5. Open Decision #5 (OpenCorporates licensing) remains UNRESOLVED. The module
   docstring in opencorporates.py carries a prominent warning.

### Assumptions
- httpx is already present (FastAPI transitive dep) — used as default HTTP client.
- python-whois and dnspython are NOT in pyproject.toml; default clients gracefully
  degrade (raise RuntimeError) if absent. Tests inject fakes — no new deps required.

---

## Implementation Plan (executed)

### P1-T4
1. Create `backend/app/adapters/__init__.py`
2. Create `backend/app/adapters/base.py` — AdapterContext, AdapterSuccess,
   AdapterFailure, SourceAdapter Protocol
3. Create `backend/app/adapters/opencorporates.py` — OpenCorporatesAdapter +
   QueryRegistriesStage; HttpClient protocol for injection
4. Create placeholder `backend/app/adapters/domain.py` and
   `backend/app/pipeline/consistency.py` for orchestrator import compatibility
5. Register all four stages in `orchestrator.default_stages()`
6. Create `backend/tests/adapters/__init__.py`,
   `backend/tests/adapters/test_base.py`,
   `backend/tests/adapters/test_opencorporates.py`
7. Append P1-T4 entry to `docs/implementation-notes.md`
8. Lint + test → commit

### P1-T5
1. Replace placeholder `domain.py` with full DomainAdapter + AnalyzeDomainStage
2. WhoisClient, DnsClient, SslClient protocols; default implementations for
   python-whois / dnspython / stdlib ssl
3. Risk signals: recently_registered, no_mx
4. Create `backend/tests/adapters/test_domain.py`
5. Append P1-T5 entry to `docs/implementation-notes.md`
6. Lint + test → commit

### P1-T6
1. Replace placeholder `consistency.py` with full ConsistencyChecksStage
2. _names_match, _iso_match, _address_match helpers; _FIELD_SPECS list
3. _run_comparisons persists FieldComparison rows with match_status
4. Create `backend/tests/pipeline/test_consistency.py`
5. Append P1-T6 entry to `docs/implementation-notes.md`
6. Update `docs/BUILD_PLAN.md` ticket statuses + Current Status
7. Lint + test → commit

---

## Code Changes

### File: backend/app/adapters/__init__.py
- Change summary: New package init.

### File: backend/app/adapters/base.py
- Change summary: Adapter contract. AdapterContext dataclass, AdapterSuccess,
  AdapterFailure (typed failures), SourceAdapter Protocol (runtime_checkable).
  FailureKind Literal["timeout","unavailable","not_found","rate_limited"].

### File: backend/app/adapters/opencorporates.py
- Change summary: Tier-1 OpenCorporates adapter. Injectable HttpClient protocol.
  Queries /v0.4/companies/search. Emits Evidence rows for company_name,
  registration_number, registration_status, jurisdiction, legal_address.
  Includes QueryRegistriesStage pipeline wrapper. Module docstring warns about
  Open Decision #5 (licensing unresolved).

### File: backend/app/adapters/domain.py
- Change summary: Tier-2 domain adapter. Injectable WhoisClient/DnsClient/SslClient.
  Emits domain_creation_date, domain_age_days, domain_registrar, domain_expiry_date,
  mx_records, no_mx, spf_record, dkim_present, ssl_issuer, ssl_subject,
  recently_registered. AnalyzeDomainStage pipeline wrapper.

### File: backend/app/pipeline/consistency.py
- Change summary: ConsistencyChecksStage. Reads Evidence rows from DB for current
  run, compares against context["normalized"] using field-specific match logic.
  Persists FieldComparison rows with match_status "match"/"mismatch"/"unverified"
  and evidence_id reference.

### File: backend/app/pipeline/orchestrator.py
- Change summary: default_stages() now returns 4 stages in order:
  NormalizeInputStage → QueryRegistriesStage → AnalyzeDomainStage →
  ConsistencyChecksStage.

### File: backend/tests/adapters/test_base.py
- Change summary: 12 tests for base contract (dataclasses, protocol satisfaction).

### File: backend/tests/adapters/test_opencorporates.py
- Change summary: 13 tests — happy path, typed failures (timeout/rate-limited/
  unavailable/not-found), stage DB persistence. All offline.

### File: backend/tests/adapters/test_domain.py
- Change summary: 18 tests — long-lived domain trust signals, recently-registered +
  no-MX risk signals, WHOIS unavailable partial results, stage persistence. Offline.

### File: backend/tests/pipeline/test_consistency.py
- Change summary: 21 tests — name/country/address match, country mismatch,
  unverified-when-no-evidence, stage persistence, evidence_id attribution. Offline.

---

## Acceptance Criteria Mapping

- **Adapter contract (base.py)**: fetch(context) -> AdapterResult; typed failures
  never raise out of pipeline — satisfied.
- **Evidence carries source attribution + confidence + tier**: every Evidence row
  emitted by both adapters has source=, tier=, confidence=, attribution= — satisfied.
- **OpenCorporates: registry evidence (existence, status, jurisdiction, identifiers,
  legal address)**: fields company_name, registration_number, registration_status,
  jurisdiction, legal_address emitted — satisfied.
- **Provider timeout → typed `unavailable`**: _TimeoutHttpClient fake raises
  TimeoutError → AdapterFailure(kind="timeout") — verified by test.
- **No match → `not_found` with zero evidence**: empty companies list →
  AdapterFailure(kind="not_found") — verified by test.
- **Domain adapter: WHOIS age, DNS, MX, SPF/DKIM, SSL, registrar**: all present in
  domain.py evidence fields — satisfied.
- **Recently-registered + no-MX → elevated-risk signals**: recently_registered and
  no_mx evidence fields emitted — verified by tests.
- **WHOIS unavailable → typed unavailable section; run continues**: WhoisUnavailable
  fake → whois_status evidence; AdapterSuccess still returned — verified by test.
- **match / mismatch / unverified FieldComparison**: all three statuses tested and
  persisted — satisfied.
- **No discovered value → unverified (not mismatch)**: explicit AC — verified by
  test_no_evidence_for_field_is_unverified.
- **FieldComparison references supporting evidence**: evidence_id set on match —
  verified by test_stage_sets_evidence_id_on_match.

---

## Build Plan Mapping

- P1-T4: Complete (2026-05-26). Open Decision #5 still unresolved — license warning
  in module docstring; no API key hard-coded.
- P1-T5: Complete (2026-05-26). python-whois / dnspython optional; default clients
  degrade gracefully if absent.
- P1-T6: Complete (2026-05-26). Three fields (company_name, country_iso,
  billing_address). Additional fields deferred to scoring/P2.

---

## Validation

### P1-T4
- `ruff check .` → All checks passed
- `ruff format --check .` → 40 files already formatted
- `pytest -q` → 84 passed in 0.70s

### P1-T5
- `ruff check .` → All checks passed
- `ruff format --check .` → 43 files already formatted
- `pytest -q` → 100 passed in 0.72s

### P1-T6
- `ruff check .` → All checks passed
- `ruff format --check .` → 44 files already formatted
- `pytest -q` → 121 passed in 0.79s

All tests run offline (SQLite + injected fakes). No live Redis, Postgres, or
network required.

---

## Open Issues

- **Open Decision #5 (OpenCorporates licensing)** — UNRESOLVED. The adapter
  targets the public API with no authenticated key. Production deployment requires
  a license agreement with OpenCorporates. Recorded in module docstring and
  implementation-notes.md.
- **DKIM probe is a presence-only check** using the `_domainkey.<domain>` base
  label. A full implementation would iterate common selectors (google, default,
  selector1, selector2). Deferred to P2-T5 (adapter robustness).
- **Domain adapter default clients require optional deps** (python-whois,
  dnspython) not in pyproject.toml. They raise RuntimeError if absent.
  Add to dependencies when enabling production use.
- **Consistency checks cover 3 fields** (company_name, country_iso,
  billing_address). Domain, tax_id, phone comparisons deferred to P1-T7 / P2-T6
  where scoring assigns weights to these signals.

---

## BUILD_PLAN Update

- P1-T4: Complete (2026-05-26)
- P1-T5: Complete (2026-05-26)
- P1-T6: Complete (2026-05-26)
- Current ticket updated to: P1-T7 — Minimal risk assessment + report assembly
- Blocker noted: Open Decision #5 (OpenCorporates licensing) for production use
- Recommended next: P1-T7
