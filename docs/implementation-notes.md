# Implementation Notes

---

## 2026-05-27 — P2-T7: Explainability payload + triage tiers

**`triage.py` derives `TriageResult` from `ScoringResult` — NEVER auto-approves.**
`derive_triage()` has two paths: (1) critical-signal override — `sanctions_hit`,
`sanctions_hit_fraud_flag`, or `ip_asn_reuse_high` force `escalate` regardless of
score; (2) score-threshold path using the thresholds imported from `engine.py`
(`TRIAGE_PRE_CLEAR_MAX=30`, `TRIAGE_ESCALATE_MIN=70`).  `TriageResult.advisory_note`
is always set and explicitly states human approval is required.  `TriageResult` has
no `decision`, `approved`, or `rejected` attribute.

**`explain.py` builds `ExplainabilityPayload` from `ScoringResult` + evidence rows.**
The payload has a `LayerExplanation` per layer (entity, infrastructure, representation,
risk), each listing `SignalDetail` objects with `evidence_ids` and the distinct
`sources` that contributed evidence to that layer's signals.  `MismatchDetail` list
includes all field comparisons (match, mismatch, unverified).  `source_coverage` maps
source → `{tier, evidence_count}`.

**`report.py` extended to include `triage` + `explainability` in the summary JSON.**
`_build_summary()` adds two new top-level keys: `triage` (TriageResult dict) and
`explainability` (ExplainabilityPayload dict).  Both are derived from the persisted
`RiskAssessment.contributing_signals` JSON by reconstructing lightweight `Signal`
objects — no re-run of the full scoring engine.  This keeps the report assembly
idempotent and fast.

**Downstream FE/API can read triage tier + explainability from report summary.**
No new endpoints or UI changes are made (P2-T8/T11 scope); the data is in the
existing `GET /reports/{run_id}` `summary` field.

**356 tests pass** (332 post-P2-T6 + 24 new triage tests).

---

## 2026-05-27 — P2-T6: Full four-layer scoring + signal catalog

**Full PRD signal catalog in `signals.py`, engine delegates layer functions.**
`engine.py` delegates each of the four layer computations to dedicated functions in
`signals.py`.  The lazy-import pattern (imports inside layer helper functions, not at
module top) prevents the circular import that would arise because `signals.py` imports
`Signal` from `engine.py`.

**Cross-submission IP/ASN reuse implemented here (deferred from P2-T2).**
`fraud_staging_risk_signals()` accepts an optional `db` session and `run_id`.  When
both are provided, `_cross_submission_reuse_signals()` queries Evidence rows from
*other* runs sharing the same ASN.  One reuse hit → `ip_asn_reuse` (elevated, w=0.3);
≥3 hits → `ip_asn_reuse_high` (elevated, w=0.6).  This is in `signals.py`, not the
adapter, per the P2-T2 note that reuse detection is a scoring concern.

**Absence signals have `evidence_ids=[]` — this is intentional.**
Signals like `no_registry_evidence` document the *absence* of data, not a finding.
They are elevated but with `evidence_ids=[]`.  The invariant "every finding signal
MUST cite ≥1 evidence row" is satisfied for all signals that represent a finding.
Tests cover both paths.

**Scoring is advisory — no approve/reject field.**  `ScoringResult` has no
`decision`, `approved`, or `rejected` attribute.  `triage_tier` maps only to
`pre_clear` / `review` / `escalate`.  Pre-clear requires human sign-off (per PRD
§ Non-Goals).  Tests explicitly assert no approval state.

**332 tests pass** (expanded from 302 baseline with 30 new signal + engine tests).

---

## 2026-05-27 — P2-T5: Adapter robustness — caching, rate-limiting, graceful degradation

**In-process dict cache with no Redis dependency.** `AdapterCache` is a
thread-safe in-memory dict keyed by `(source, lookup_key)` with per-source TTL
and FIFO eviction at `max_size`.  Redis-backed distributed caching is a P3
upgrade; the interface (`get`/`set`/`invalidate`/`clear`) is intentionally
minimal to make that swap trivial.

**Per-source TTL table is defined in `cache.py` as `DEFAULT_TTLS`.**  Sources
with stable data (OpenCorporates: 24h) have long TTLs; sources with higher
freshness requirements (web: 30min) have short TTLs.  Callers can override
per-source TTLs at construction time.

**`DEFAULT_TTLS` takes precedence over `default_ttl` constructor arg.** The
`AdapterCache` merges `DEFAULT_TTLS` with the caller-supplied `ttls` dict.  A
test that used `default_ttl=0` with `"opencorporates"` as the source was hitting
the `DEFAULT_TTLS` value (86400s) rather than the override.  Tests use a custom
source name not in `DEFAULT_TTLS` when testing TTL expiry.  This is documented
in the test file.

**Token-bucket rate limiter with jittered exponential backoff.** `RateLimiter`
uses a token bucket per source.  When the bucket is empty, `acquire()` sleeps
with jitter (base * [0.5, 1.0]) rather than failing immediately — this matches
ARCHITECTURE § 4: "a source that stays down is recorded as unavailable, not a
failed run".  Max-wait per source is configurable.

**`SourceAvailabilityTracker` is the run-level coverage summary.**  Records
`available` / `unavailable` per source after each adapter result.  The scoring
stage (P2-T6) reads this to adjust confidence when coverage is reduced.  The
tracker is instantiated per run; it is not persisted to the DB in this ticket —
that integration is deferred to P2-T6 where the scoring engine reads it.

**Wrappers are transparent — no change to public evidence output.**  Both
`CachedAdapter` and `RateLimitedAdapter` proxy `name`, `tier`, and `fetch()`.
Callers that wrap an existing adapter see the same `AdapterResult` contract.
Applying these wrappers to existing adapters (opencorporates, domain, ipinfo,
sanctions, web) is left to the caller's wiring (e.g. `default_stages()`) and
is deferred to when the scoring/pipeline integration ticket (P2-T6) adds the
full run-level coverage model.

---

## 2026-05-27 — P2-T4: Tier-3 public web evidence + contact extraction

**Playwright is lazy-imported inside `_PlaywrightFetcher.__init__` only.**
It is never imported at module level.  Tests verify this via
`test_playwright_not_imported_at_module_level` which checks `sys.modules`
after importing `app.adapters.web`.  CI without `playwright install` passes
freely; `playwright` is not added to pyproject.toml deps at this time because
tests use a fake fetcher and the production path uses httpx first.  Adding
`playwright` as an optional dep (e.g. `[extras]`) is a P3 consideration.

**Contact extraction uses regex over stripped HTML (no BeautifulSoup).**
Email, phone, and street-address patterns cover the common cases.  BeautifulSoup
would be more robust for malformed HTML; it is not added as a dep to keep scope
minimal.  The regex approach is sufficient for the Tier-3 "supporting evidence"
use case — these contacts are treated as lower-confidence signals (0.55–0.70)
and always have source attribution for operator review.

**Branding extraction priority: og:site_name → og:title → <title> → <h1>.**
og:site_name is the most reliable brand signal; title tags often include
page-specific suffixes ("— Blog") so og:site_name is preferred.

**Employee footprint is a proxy count (emails on page), not a real headcount.**
The `web_employee_footprint` field records the count of unique email addresses
found on the page.  A thin site with no emails emits `web_thin_footprint`.
This is an MVP-level approximation; a richer footprint analysis (LinkedIn,
job boards) is deferred to a future ticket.

**`WebEvidenceStage` was pre-registered in orchestrator in P2-T3 commit.**
The orchestrator already imports `WebEvidenceStage` as of P2-T3 (to avoid an
import error when both the sanctions and web stages were wired together).  This
commit adds the full test suite; no orchestrator change needed.

---

## 2026-05-27 — P2-T3: Sanctions/watchlist screening via public OFAC SDN list

**Government business registries are explicitly deferred (Open Decision #5).**
The P2-T3 ticket covers both "gov registries" and "sanctions/watchlist".  Gov
registries require per-country licensing and variance resolution (#5) before any
adapter can be built safely; building stubs would be misleading about coverage.
Only the sanctions/watchlist portion is implemented here; the gov-registry half is
a follow-on ticket once Open Decision #5 is resolved.

**OFAC SDN list chosen as the concrete sanctions source.** The OFAC SDN list
(https://www.treasury.gov/ofac/downloads/sdn.csv) is free, publicly downloadable
with no licensing barrier, and is the canonical US sanctions list.  The fetcher
is injectable so tests use a small deterministic fixture — no live download in tests
or CI.

**Name matching is exact after normalization (suffix-stripping + lowercase), not
fuzzy.** Substring matching would produce false positives ("IRAN LLC" would match
"Iran").  Legal-suffix stripping (Ltd, Inc, LLC, Corp, GmbH, etc.) + lowercase
normalization is applied to both the query and each list entry before comparison.
Full fuzzy/phonetic matching (Levenshtein, Soundex) is deferred — conservative
by design to minimize false positives on a sanctions list.

**The SDN list is fetched once and cached per adapter instance.** The production
pipeline creates one adapter instance per run; the list is fetched once at first
call and cached in-memory.  A production deployment should implement a shared
cache (e.g. Redis-backed with a TTL of ~24h) to avoid re-downloading on every
run.  This is a P3 optimization.

**`SanctionsScreeningStage` registered after `query_registries`, before
`analyze_domain`.** This maintains ARCHITECTURE § 2 ordering: all Tier-1
authoritative sources (registries + sanctions) run before Tier-2 infrastructure.
The orchestrator `default_stages()` comment is updated to reflect the new order.

**`WebEvidenceStage` stub also registered in orchestrator for P2-T4.** The
orchestrator imports both new stages.  The web adapter is created in the same
commit to avoid import errors during the P2-T3 test run.

---

## 2026-05-27 — P2-T2: Network/IP intelligence enrichment

**Production IPINFO_TOKEN is unresolved — free tier is the default.** The
`IPInfoAdapter` reads `IPINFO_TOKEN` from the environment; if absent it uses
the anonymous free tier (no API key).  The free tier has strict rate limits and
omits the `privacy` sub-object (hosting/VPN/proxy flags).  A free-tier fallback
uses org-name keyword matching for datacenter detection at 0.70 confidence (vs
0.85–0.95 for explicit privacy flags).  Production deployment should set
`IPINFO_TOKEN`; obtaining a paid plan is the same class of open decision as
Open Decision #5.  This is documented in the module docstring.

**`source_ip` added to `context["normalized"]` by `NormalizeInputStage`.**
The IPinfo stage reads `source_ip` from the pipeline context (not from a second
DB query).  The normalize stage already reads the `Submission` row — adding
`source_ip` there is zero cost and avoids a second DB hit in the IPinfo stage.
This is a minimal addition to the normalize contract; no existing tests broke.

**ASN reuse detection is a scoring/consistency concern, not an adapter concern.**
The adapter emits `ip_asn` evidence on every enriched run.  Cross-submission
reuse patterns (e.g. repeated registrations from the same ASN) are a P2-T6
scoring signal, not something a single-run adapter can determine.  The test
documents this via `test_asn_evidence_is_emitted_for_reuse_detection`.

**`ip_suspicious_asn` flag requires both keyword match AND anonymized=True.**
Emitting a suspicious-ASN flag purely from org-name keywords (e.g. "Google
LLC") would produce false positives for residential ISPs with cloud subsidiaries.
The flag is gated on `anonymized` also being True — conservative by design.

---

## 2026-05-27 — P2-T1: Entity candidate resolution stage

**Single candidate from submission itself for MVP.** `_build_candidates` returns
one candidate synthesised from the submitted name + domain.  The conflict-
detection path (two candidates within the gap) is exercised by patching
`_score_and_rank` in tests, demonstrating the logic is wired correctly for
when P2-T3 injects additional registry candidates.

**Conflict detection patches `_score_and_rank`, not `_build_candidates`.**
Tests that verify the conflict window patch `_score_and_rank` (the output of
scoring) rather than `_build_candidates` (the raw input list).  This is because
`_score_and_rank` re-scores every raw candidate — injecting pre-scored candidates
via `_build_candidates` had no effect on the final scores.  Patching at the
correct boundary (the ranked output) tests the actual logic.

**No Evidence rows are persisted by this stage.** Candidate resolution is an
intermediate pipeline computation, not a source-attributable finding.
Evidence for the `conflict_signal` risk factor will be produced by the scoring
stage (P2-T6) which has the full picture of all stage outputs.

**Stage is deterministic and fully offline.** No network I/O, no DB writes —
only reads from `context["normalized"]`.  This means the stage never degrades
the run and never writes to `source_availability` as "unavailable".

---

## 2026-05-26 — P1-T10: Operator app — list + detail + mark reviewed

**Added `GET /reports/` list endpoint to backend** (not present in P1-T8).  The
dashboard required a list; P1-T8 only exposed `GET /reports/{run_id}`.  Added a
minimal `list_reports` route that resolves company name/domain via the
submission FK chain, reads `overall_score` from the JSON summary, and joins to
the Review table for `review_status`.  N+1 queries per report row are acceptable
for MVP scale.  Batching (JOIN or subquery) is a P3 optimization.

**Hash-based routing instead of react-router.**  The task instructions said
"prefer minimal routing" and specifically noted react-router "only if needed."
The operator app has two views (dashboard and detail), so a simple `useState`
with a discriminated union route covers it without adding a dependency.

**No localStorage for session token.**  Token is stored in React state only
(clears on page refresh).  This is intentional for the MVP — avoids XSS
persistence risk.  The tradeoff is that refreshing forces re-sign-in.  A
future ticket (P2+) can persist to sessionStorage or use a cookie.

**`AuthContext` exported alongside components in one file.**  ESLint's
`react-refresh/only-export-components` rule triggers when a non-component is
exported from the same file.  Suppressed at file level with
`eslint-disable react-refresh/only-export-components`.  The alternative —
splitting AuthContext into its own file — adds indirection with no functional
benefit for MVP.  Revisit if fast-refresh issues surface in local dev.

**Phase 1 exit criteria met.**  The full MVP vertical slice is now in place:
submitted registration → pipeline → stored report → operator sign-in (audited)
→ list view → detail view (submitted vs. discovered diff with match/mismatch
indicators + risk score) → mark reviewed (audited audit_event).

---

## 2026-05-26 — P1-T9: Operator auth + append-only audit foundation

**Session store is in-memory (dict) for MVP.** A single `_session_store: dict`
maps opaque tokens to operator_ids.  Appropriate for a single-process MVP dev
environment.  The replacement path for OIDC/multi-process: (1) swap
`_session_store` for a Redis-backed TTL store or JWT validation, (2) keep
`get_current_operator()` signature unchanged — routes need no changes.  This
is documented in the module docstring.

**Password hashing uses PBKDF2-HMAC-SHA256 via stdlib `hashlib` — no new deps.**
260,000 iterations (NIST SP 800-132).  Hash format is self-describing:
`"pbkdf2_sha256:<iterations>:<salt_hex>:<digest_hex>"`.  Swap for argon2/bcrypt
in P3 — the verify_password() interface stays the same.  No new Python packages
were added.

**`password_hash` column added to the Operator model.** Nullable (Text), so
future OIDC-only accounts can omit it.  This is a schema addition that needs an
Alembic migration for production Postgres.  SQLite tests use `create_all()` which
picks it up automatically.  Production migration is flagged for when a live
Postgres is provisioned.

**`record_event()` issues a `db.commit()` inside its own call.** This ensures
the audit row is persisted independently of the caller's transaction lifecycle.
The caller (e.g. `mark_reviewed`) calls `db.flush()` first (to get review.id),
then `record_event()` (which commits), then returns the response.  If the route
were to raise after `record_event()`, the audit row would still be persisted.
This is a deliberate durability choice for the audit trail.

**`require_lead()` is a FastAPI dependency, not a middleware.** It takes the
already-resolved `operator` from `get_current_operator()` and checks the role.
This is simpler than middleware for per-route RBAC and consistent with how
FastAPI recommends structuring role-based access.

**No auto-approval path in mark-reviewed.** The endpoint writes `review.status`
as-provided by the operator body (default `"reviewed"`).  The `"approved"` value
is a review status tag, not an EntityIQ system action — the human operator makes
the approval decision.

---

## 2026-05-26 — P1-T8: Report retrieval API

**Report serialization reads pre-assembled summary JSON, not live joins.**
`_serialize_report()` reads `report.summary` (assembled by `StoreReportStage`)
rather than re-joining Evidence + FieldComparison + RiskAssessment on every
API call.  Trade-off: summary is slightly stale between stage runs; payoff: fast
reads and no complex join logic in the API layer.

**404 distinguishes "unknown run" from "report not yet assembled".**
Two separate 404 paths: (1) `VerificationRun` not found → run_id unknown;
(2) `Report` not found → run exists but pipeline hasn't stored the report yet.
Different detail strings allow callers to handle these cases differently.

**`_get_db` is a separate dependency per router.** The reports router has its
own `_get_db` (not shared with the submissions router) so its DB fixture can
be overridden independently in tests.  The submissions conftest only overrides
`app.api.submissions._get_db`; the reports conftest overrides
`app.api.reports._get_db`.  Both share the same SessionLocal factory in
production.

---

## 2026-05-26 — P1-T7: Risk scoring v1 + report assembly

**Scoring is purely advisory — no approve/reject field exists.** `ScoringResult`
has no `decision`, `approved`, or `rejected` attribute.  `triage_tier` maps only
to `"pre_clear"`, `"review"`, or `"escalate"` — none are approval states.  Tests
explicitly assert this.

**Signals with no evidence IDs are intentional for "unavailable" cases.**
When a source is entirely missing (no evidence at all), the engine emits an
`elevated` signal like `"no_registry_evidence"` with `evidence_ids=[]`.  This is
acceptable because the signal explains the *absence* of evidence, not a finding from
evidence.  The invariant "every finding must cite evidence" is satisfied for all
signals that *do* cite findings; absence signals document what is missing.

**Scores reflect uncertainty, not confirmed low-risk, when sources are absent.**
`_score_entity_legitimacy` returns 60 (not 0) when no Tier-1 evidence is available.
`_score_infrastructure_legitimacy` returns 40 when no Tier-2 data is found.
Both are above `TRIAGE_PRE_CLEAR_MAX=30`, so missing sources can never yield a
`pre_clear` outcome on their own.

**Layer weights are equal (0.25 each) in v1.** Full weighted scoring with
per-signal catalog is P2-T6.  Equal weights are explicit and documented so the
change in P2-T6 is a targeted update, not a discovery.

**`StoreReportStage` is idempotent.** `assemble_report` uses a query-then-upsert
pattern (create if none, update if exists).  Called after `ScoringStage` in the
default pipeline; can be re-called on re-analysis without leaving stale rows.

**`Report.summary` is denormalized JSON for fast API reads.** Rather than
requiring the API layer to join Evidence + FieldComparison + RiskAssessment on
every request, the summary is pre-assembled at pipeline time.  This is updated
on every `StoreReportStage` call including during partial runs.

---

## 2026-05-26 — P1-T6: Consistency checks → field comparisons

**Three fields checked: company_name, country_iso, billing_address.** These are
the core submitted fields with corresponding evidence fields (company_name →
company_name, country_iso → jurisdiction, billing_address → legal_address).
Additional fields (domain, tax_id, phone) are deferred to P1-T7 / P2-T6 where
scoring assigns weights.

**Match logic is intentionally loose (substring / token-overlap), not exact.**
Registry names often include "Ltd", "Inc", "GmbH" suffixes not in the submission.
Addresses may abbreviate "Street" as "St". A strict equality check would produce
false mismatches. The 40% token-overlap threshold for addresses and substring
matching for names balance precision vs. recall at MVP scope.

**"unverified" is the correct status when no evidence exists, not "mismatch".**
This is explicitly specified in the acceptance criteria. If a Tier-1 adapter
returned not-found, there is no basis for a mismatch verdict.

**jurisdiction_code prefix comparison.** OpenCorporates returns jurisdiction
codes like "us_ca" (US state of California). We compare the ISO country prefix:
"US" submitted vs "us_ca" evidence → match. This avoids false country mismatches
when entity is incorporated in a specific state.

---

## 2026-05-26 — P1-T5: Tier-2 domain/infrastructure signals adapter

**Three injectable clients (WHOIS, DNS, SSL) allow fully offline tests.** No
new runtime dependencies are added — the default WHOIS client looks for the
optional `python-whois` package; the default DNS client looks for `dnspython`.
If either is absent, the default client raises `RuntimeError` (so a real
production deployment would need them installed, but tests inject fakes).
The SSL client uses stdlib `ssl` + `socket` — no extra dep.

**DKIM probe uses `_domainkey.<domain>` as a presence signal only.** A real
implementation iterates known selectors (google, default, selector1, etc.).
This is a presence probe — "DKIM record exists at the common base selector" —
which is sufficient for a Tier-2 signal. Full selector enumeration is deferred.

**`had_any_data` flag ensures we distinguish "all failed" from "no evidence".**
If all three lookups (WHOIS, DNS, SSL) raise, `had_any_data` stays False and the
adapter returns `AdapterFailure(kind='unavailable')`. If any one succeeded, we
return `AdapterSuccess` — even if some lookups failed — so partial results are
preserved.

**recently_registered threshold is 180 days.** This matches PRD § Risk Signals
("recently registered domain"). The constant `_RECENTLY_REGISTERED_DAYS = 180`
is module-level so it can be overridden in testing.

---

## 2026-05-26 — P1-T4: Source adapter interface + OpenCorporates Tier-1 adapter

**Open Decision #5 (data-source licensing) remains OPEN.** The OpenCorporates
adapter targets the public API (https://api.opencorporates.com/v0.4).  A
prominent warning in the module docstring states that production use requires a
license agreement.  No API key is hard-coded.  Resolution of #5 is required
before this adapter is enabled in production.

**HTTP client is injectable via constructor parameter.** `OpenCorporatesAdapter`
accepts an optional `http_client` argument (any object satisfying the `HttpClient`
protocol).  Tests inject a `_FakeHttpClient` with a pre-configured `_FakeResponse`
— zero network calls.  Production path uses `httpx.Client()` (already present as
a transitive dep of FastAPI; not a new dependency).

**Typed failures via `AdapterResult` union.** All four failure kinds (timeout /
unavailable / not-found / rate-limited) are returned as `AdapterFailure(kind=...)`
dataclasses — never as raised exceptions.  The pipeline stage wrapper
(`QueryRegistriesStage.run`) checks `isinstance(result, AdapterSuccess)` and logs
the failure kind into `context["registries"]["status"]`.

**Placeholder stubs for domain.py and consistency.py.** `orchestrator.default_stages()`
imports all four stage classes at call time.  To keep P1-T4 self-contained and
passing, minimal placeholder classes (`AnalyzeDomainStage`, `ConsistencyChecksStage`)
were created.  They will be replaced by full implementations in P1-T5 and P1-T6
respectively.  This is expected and noted here to avoid confusion.

**Evidence rows are not persisted in unit tests for the adapter-only tests.**
The `test_base.py` and the pure fetch() tests in `test_opencorporates.py` do not
persist to a DB — they test the adapter contract in isolation.  The
`test_stage_persists_evidence_on_match` test uses a full SQLite session and
verifies DB persistence via `QueryRegistriesStage`.

---

Running log of decisions, deviations, tradeoffs, and surprises during
implementation. Written for human review, tied to build-plan tickets.

---

## 2026-05-26 — P0-T1: Resolve blocking Open Decisions

**Decision (Open Decision #1 — backend stack): Python + FastAPI.** Confirmed by the
user. Rationale per ARCHITECTURE.md: the verification/enrichment/parsing domain
(WHOIS/DNS, address/phone normalization, sanctions, Playwright extraction) — which
*is* the product — has the strongest ecosystem in Python. Frontend stays React+TS.
Alternative (Node/TS) was rejected; it would unify language with the FE but weaken
the enrichment ecosystem.

**Decision (Open Decision #2 — job orchestration): Celery + Redis.** Follows from #1;
mature and simple fit for the async per-run pipeline model.

**Decision (Open Decision #3 — `shared/` contents): OpenAPI-generated TS types.** The
FastAPI backend emits an OpenAPI schema; the frontend consumes generated TS types
rather than hand-maintained duplicates. `shared/` holds the contract, not runtime code.

**Still open / deferred:** #4 HQ map provider, #5 data-source access/licensing,
#6 co-primary tiebreaker, #7 PII retention policy — to be resolved at the tickets
that need them (per BUILD_PLAN.md).

**Effect:** unblocks P0-T2 (monorepo scaffold). All build tickets now have a
confirmed stack.

---

## 2026-05-26 — P0-T2: Monorepo scaffold

**`vite.config.ts` added to frontend/.** The ticket spec listed `package.json`,
`tsconfig.json`, `index.html`, `src/main.tsx`, `src/App.tsx` as the FE files.
`vite.config.ts` is not listed but is required for `@vitejs/plugin-react` to load
(Vite errors without it when the plugin is declared). Treated as part of the
minimal runnable skeleton, not a scope expansion. Includes a `/api` proxy entry
pointing at the backend dev server (`localhost:8000`) to avoid CORS friction in
local dev.

**`@types/react` and `@types/react-dom` added to devDependencies.** Required for
TypeScript to compile `.tsx` files. Not a new runtime dependency.

**`hatchling` chosen as build backend for `pyproject.toml`.** Zero-config for a
flat `app/` layout; no `src/` wrapper needed. Consistent with the "simple over
clever" rule. Alternative (`setuptools`) would require a `setup.cfg` or explicit
`find:`. No difference in practice at scaffold time.

**Dependency install deferred.** Neither `pip install` nor `npm install` was run.
Deps are correctly declared; install/verify steps are documented in
`docs/implementation.md § Validation`. P0-T3 will establish the canonical install
path in CI.

---

## 2026-05-26 — P0-T3: Lint, test harness, and CI

**Backend deps pinned to exact versions to avoid pip backtracking.** The local
Python is 3.10.10 (pyproject.toml targets 3.11; CI images use 3.11-slim and will
be fine). Open-ended `>=` constraints caused the pip resolver to download 80+
ruff/pytest/httpcore wheels locally. Fixed by pinning to known-compatible exact
versions in `pyproject.toml`: `fastapi==0.111.0`, `starlette==0.37.2`,
`pydantic==2.7.4` (runtime), `pytest==8.3.5`, `httpx==0.27.2`, `ruff==0.4.10`
(dev). Tradeoff: slightly more maintenance overhead on upgrades; payoff: reliable
CI installs. In CI (Python 3.11-slim), the constraints are the same and will
resolve identically.

**`httpx` added as dev dep.** FastAPI's `TestClient` wraps `httpx`; omitting it
caused an `ImportError` at test collection time. Not a new runtime dep.

**`[tool.hatch.build.targets.wheel] packages = ["app"]` added to pyproject.toml.**
Hatchling couldn't auto-discover the package because the directory name `app/`
doesn't match the project name `entityiq-backend`. This is a build config fix,
not a scope expansion; it's required for `pip install .` to succeed in CI.

**Frontend ESLint uses the `.eslintrc.cjs` (legacy) format.** The project uses
`"type": "module"` in `package.json`, which means `.eslintrc.js` would fail in CJS
mode. `.eslintrc.cjs` forces CommonJS evaluation, which is the correct pattern for
eslint v8 with ESM projects.

**vitest config embedded in `vite.config.ts`** (via `/// <reference types="vitest" />`
triple-slash directive and a `test:` block). This avoids a separate config file and
is the recommended pattern for Vite + vitest setups.

**`@testing-library/react` without `@testing-library/jest-dom`.** The two test
assertions (`getDefined()`) use vitest's built-in `expect` with `.toBeDefined()`,
so `jest-dom` matchers are not needed. Kept scope minimal.

**Validation results (all passing locally):**
- `ruff check .` → "All checks passed!"
- `ruff format --check .` → "4 files already formatted"
- `pytest -v` → 2 passed in 0.77s
- `npm run lint` → exit 0, no warnings
- `npm run test` (vitest run) → 2 passed in 1.47s

---

## 2026-05-26 — P1-T3: Pipeline stage: normalize input

**normalize.py was created in P1-T2 as a stub required by default_stages().**
The full implementation (country map, domain/email/address functions,
NormalizeInputStage class) was delivered in the same file. The P1-T3 commit
adds tests and updates the module docstring. No functional change from P1-T2.

**country map covers ~40 common aliases/codes.** The design assumption is that
a submitted country of "US", "usa", "United States", or "united states of america"
all map to "US". The map is extended as new locales surface. Full ISO 3166-1
coverage is not the goal (no external dep for this MVP stage).

**Format_tax_id is a stub (stripped value only).** Country-specific tax ID
formatting (e.g. EIN for US, VAT number for EU, CIF for Spain) requires per-
country logic and is deferred to the adapter/source tickets (P1-T4+) where the
format becomes relevant for registry lookups.

**NormalizeInputStage does not persist Evidence rows.** The ticket scope is
context production for later stages. Evidence persistence is a concern of the
stages that have a source to attribute (Tier 1/2/3 adapters). Adding an
'normalize' evidence row now would be premature and untestable without P1-T4's
adapter interface.

---

## 2026-05-26 — P1-T2: Async run orchestration skeleton

**Celery task_always_eager vs. direct run_sync() for tests.** The submission
tests use `mock.patch("app.pipeline.orchestrator.enqueue_run")` (no-op) so the
API tests don't need Redis. The orchestrator tests call `Orchestrator.run_sync()`
directly with a SQLite session — no Celery involved. This gives clean isolation:
API tests verify the HTTP layer; orchestrator tests verify the pipeline logic.
The `CELERY_TASK_ALWAYS_EAGER` env var remains available for future integration
tests that want to exercise the full Celery path.

**task_always_eager also kept as env-var config in worker.py.** Retained for
production-adjacent staging environments that set `CELERY_TASK_ALWAYS_EAGER=true`
to run synchronous verification (e.g. smoke tests against a staging DB). This is
a deployment concern, not a unit-test concern.

**run_verification_task opens its own SessionLocal().** The Celery worker runs in
a separate process and must open its own DB session. This means `task_always_eager`
integration would require DATABASE_URL pointing to a real DB. Acceptable — the
unit tests bypass the Celery path entirely.

**`default_stages()` is a function, not a module-level list.** Lazy import of
each stage (NormalizeInputStage, etc.) keeps the module-level import graph
clean and allows tests to instantiate Orchestrator with custom stage lists
without importing every adapter.

**`_update_stage_status` copies the JSON dict before re-assigning.** SQLAlchemy
does not always detect in-place mutations to JSON columns. Copy-assign (new dict)
guarantees the change is tracked for the next `db.commit()`.

**Re-analysis is a helper function, not a separate endpoint.** `enqueue_reanalysis`
in orchestrator.py creates the new VerificationRun and calls `enqueue_run`. The
API endpoint for re-analysis is P2-T11 — this ticket delivers the plumbing.

---

## 2026-05-26 — P1-T1: Submission endpoint + network-metadata capture

**Trusted-IP invariant implemented via TRUSTED_PROXY_DEPTH env var (default 0).**
Default: use `request.client.host` (TCP connection peer — the edge-assigned IP).
If the operator sets `TRUSTED_PROXY_DEPTH=N` (N>0), the Nth entry from the right
of `X-Forwarded-For` is used (the entry the trusted edge appended). This is
explicitly configurable rather than implicit, matching ARCHITECTURE § 5.

**Free-email domain flag stored in response only, not in DB.**
The ticket requires flagging for downstream stages. We return `is_free_email_domain`
in the 202 response. The normalize stage (P1-T3) will persist this as evidence.
Storing it as a DB column on `submission` was considered but rejected — that field
isn't in the P0-T4 schema and adding it would require a new migration before the
normalize stage exists. Deferred to P1-T3 where it naturally belongs.

**Entity created one-per-submission in P1-T1.** Entity resolution/deduplication is
P2-T1. For now each submission spawns a new entity row. This will be corrected when
the entity-resolution stage is added — it's expected tech debt.

**`email-validator` already installed** (pulled in by fastapi extras). Added to
pyproject.toml as `pydantic[email]` is the canonical way to declare this dep; but
since `email-validator==2.2.0` was already present from fastapi, no new install was
needed. Added explicit `email-validator==2.2.0` to dev deps in pyproject.toml for
clarity.

**SQLite StaticPool in tests.** sqlite:///:memory: gives each new connection a fresh
empty DB. The conftest uses StaticPool so all sessions share one connection — this
is required to make API endpoint writes (which commit) visible to the `db_session`
fixture in the same test. The existing `test_models.py` was unaffected (it manages
its own engine/session independently).

**`noqa: PLC0415` on deferred import of enqueue_run.** The import is inside the
endpoint function to avoid a circular import at module load time (submissions →
orchestrator → potentially back to submissions). This is a known pattern for
circular-import avoidance; the noqa suppresses the ruff "import not at top of file"
warning.

---

## 2026-05-26 — P0-T4: Postgres + migrations + core data model

**Generic JSON not JSONB (portability decision).** All JSON columns use
SQLAlchemy's generic `JSON` type rather than the Postgres-specific `JSONB`.
Reason: `JSONB` only builds on Postgres; using it would break the SQLite-based
model tests in CI. In production, `json` and `jsonb` have identical semantics
for reads/writes; the difference is GIN-index support, which isn't needed until
we're querying `evidence.raw_payload` or `audit_event.payload` at scale. A
future migration can alter the columns to `jsonb` without application code
changes.

**String(36) PKs not native UUID columns (portability decision).** All primary
keys are `String(36)` with a Python-side `uuid.uuid4()` default. PostgreSQL's
native `UUID` column type and `gen_random_uuid()` server-default would be more
idiomatic but require dialect-specific DDL. This tradeoff keeps the schema
portable for the SQLite test path. Revisit when the test strategy migrates away
from SQLite (e.g., testcontainers with Postgres).

**Alembic migration generated against SQLite, not Postgres.** No live Postgres
instance was available. The migration was generated and applied against a
throwaway SQLite file (`sqlite:////tmp/entityiq_upgrade_test.db`). Alembic
renders dialect-appropriate DDL at apply time, so the migration will work
against Postgres when `DATABASE_URL` points to one — but this has not been
manually verified. Flagged as a step for P1-T1 setup.

**`per-file-ignores` for `app/db/migrations/versions/*.py` (ruff E501).**
Alembic-generated migration scripts contain long lines from auto-generated
column definitions. Added a ruff per-file-ignore for E501 in the versions
directory rather than hand-editing generated code, which would create a
maintenance burden on every future migration.

**`VerificationRun.supersedes_id` self-reference.** SQLAlchemy needs an
explicit `foreign_keys=` and `remote_side=` to resolve the ambiguous join
direction on a self-referential FK. The `superseded_run` relationship
(pointing up the chain to the run that was superseded) uses
`remote_side="VerificationRun.id"`; the `superseding_run` relationship
(pointing down to the run that supersedes this one) uses `uselist=False`.
Tested by `test_verification_run_supersedes_self_reference`.

**AuditEvent append-only contract is model-layer only.** No `.update()` or
`.delete()` methods are exposed on the class. DB-level enforcement (RLS or a
trigger) is explicitly deferred to P3-T3 per the build plan.

**Validation results (all passing locally):**
- `ruff check .` → "All checks passed!"
- `ruff format --check .` → "20 files already formatted"
- `pytest -v` → 9 passed in 1.50s (2 health + 7 new model tests)
- `alembic upgrade head` (SQLite) → "Running upgrade -> c46233bc9881, initial_schema"

---

## 2026-05-31 — P2-T8 Detail view completeness

**Decision: frontend-only.** `GET /reports/{run_id}` already returns Tier-1/2/3
evidence (with `source`/`field`/`attribution`), four-layer scores, and
`contributing_signals`. The four PRD detail panels are pure presentational
components over the existing payload — no backend/schema/pipeline changes.

**Decisions / tradeoffs:**
- One shared `components/DetailPanels.tsx` (Domain/Registry/Contact/Risk panels +
  shared FieldRow/pending/empty primitives) instead of four near-duplicate files —
  DRY, matches the focused-module pattern of `RegistrationDiff.tsx`.
- "Operator notes" delivered via an optional notes textarea on the **existing**
  Mark-Reviewed action (the `markReviewed` API already accepts `notes`). The rest
  of the operator actions (correct data, re-run, export, dashboard filters) stay
  in scope for **P2-T10**, not pulled forward.
- "DNS risk score" (PRD) surfaced as the `infrastructure_score` line + inline
  `recently_registered` / `no_mx` flag badges — there is no standalone per-DNS
  subscore in the model.
- Contact panel shows the primary discovered value per type + `source_url`
  attribution; full extracted lists live in `raw_payload` and are not exposed by
  the report API (intentional, out of scope).

**Pre-existing issue noted (not fixed — unrelated):** `tsc --noEmit` reports a
strict-null backlog across the frontend (incl. `auth is possibly null` in
CompanyDetail/Dashboard and missing test-runner globals). Present at baseline (85
errors before this change); my production files added zero new errors. The team's
gate is `npm run lint` + `vitest`, both green.

**Validation:**
- `npm run lint` → clean (0/0).
- `npm test` → 12 passed (3 files).

---

## 2026-05-31 — P2-T10 Full operator actions + dashboard filters

**Decision: frontend-only.** The backend endpoints already exist (P2-T11):
`POST /reanalysis/{run_id}`, `POST /workflow/runs/{run_id}/correct`,
`POST /workflow/runs/{run_id}/notes`, `GET /reports/{run_id}/export`. This ticket
adds typed client methods + operator UI + client-side dashboard filtering.

**Decisions / tradeoffs:**
- New `components/OperatorActions.tsx` (re-run, correct + re-run, add note, export)
  keeps `CompanyDetail` display-focused — mirrors the RegistrationDiff/DetailPanels
  split.
- Correct-and-re-run sends **only changed fields**: form prefills from the report's
  mismatches and diffs on submit, matching the backend's "only listed fields are
  updated" contract. Empty correction is blocked client-side (no request).
- Re-run / correct produce a superseding run; UI surfaces the new `run_id` and an
  `onOpenRun` link. `App` adds `key={runId}` so the detail view remounts cleanly on
  navigation to the new run.
- Export = client-side JSON download (Blob + anchor), guarded for environments
  without `URL.createObjectURL`. Formatted PDF/HTML export is out of scope.
- Dashboard filters (search company/domain, review status, risk band) are
  **client-side** over the already-fetched list — smallest change for current
  scale; server-side pagination/filtering can follow if the queue grows.

**Pattern note:** followed the codebase's existing `auth.token` direct-access
pattern (AuthProvider guarantees non-null). `tsc` flags the usual pre-existing
`auth is possibly null` warnings repo-wide; new files add none. Gate is
`npm run lint` + `vitest`.

**Validation:** `npm run lint` clean; `npm test` → 18 passed (4 files).

---

## 2026-05-31 — P2-T12 API auth for integrating systems

**Decision: backend-only.** Added an `ApiClient` service-credential table + an
API-key auth module, applied at the two integration endpoints (`POST /submissions`,
`GET /reports/{id}/export`), with per-system attribution in the audit trail.

**Decisions / tradeoffs:**
- **`X-API-Key` header for service creds**, kept separate from operator
  `Authorization: Bearer` so the two schemes never collide.
- Keys **hashed at rest** (PBKDF2, reusing the operator password primitive — no new
  dep); a non-secret 12-char `key_prefix` is stored for O(1) lookup before a
  constant-time full-key verify. Plaintext returned once by `create_api_client`.
- **Submission requires a key** (401 otherwise) and is attributed via
  `submission.api_client_id` + a `system.submission_received` audit event.
- **Export requires a `Principal`** (system key OR operator Bearer) so the operator
  UI export keeps working while machine pulls are authenticated + attributed
  (`report.exported` audit event). The operator-only report reads (`/reports/`,
  `/reports/{id}`) were left open as before — locking the full read surface is
  Phase-3 hardening, not this ticket (noted as follow-up).
- **Migration uses Alembic batch mode** so the new `api_client_id` FK columns apply
  on SQLite (copy-and-move) as well as Postgres — the initial `create_foreign_key`
  failed on SQLite (no ALTER-constraint support).
- **Test wiring:** the cross-module `_get_db` pattern means each new auth dependency
  defines its own `_get_db`; conftest now also overrides `service._get_db`, seeds a
  session-scoped credential, and sets a default `X-API-Key` header so existing
  submission tests authenticate unchanged. Two existing export tests were updated to
  pass an operator token.

**Follow-ups:** mTLS, key-provisioning UI/endpoint, Redis/JWT session store, and
locking down operator-only report reads.

**Validation:** `ruff check`/`ruff format --check` clean; `pytest` → 383 passed;
`alembic upgrade/downgrade/upgrade` on SQLite clean.

---

## 2026-05-31 — Demo: screenshot login backdrop (removed 2026-09-24)

- Decision (not in spec): for a demo, the operator sign-in screen rendered a
  full-page website screenshot as a backdrop with the sign-in card pinned on top.
- Removed 2026-09-24 when the repo was de-branded for public showcase use; the
  sign-in page now uses a neutral CSS gradient and the PNG asset is deleted.

---

## 2026-05-31 — Fix: Vite dev proxy strips /api prefix

- Bug: the operator app calls `/api/...` (see `frontend/src/api/client.ts`),
  but the Vite proxy was `"/api": "http://localhost:8000"` with no rewrite, so
  requests were forwarded verbatim to `http://localhost:8000/api/...`. The
  backend mounts routes at the root (`/auth/sign-in`, `/reports`, ...), so every
  call 404'd. Sign-in surfaced this as "Not Found". This path was never
  exercised end-to-end before (RUNBOOK flagged FE↔BE wiring as unverified).
- Fix: `frontend/vite.config.ts` now uses the object proxy form with
  `rewrite: (path) => path.replace(/^\/api/, "")` (and `changeOrigin: true`),
  matching the behavior `client.ts` already documented ("the proxy strips it").
- Scope: dev-server only; production build/output is unchanged. Verified login +
  the verification queue work end-to-end through the proxy after the change.
- Validation: `npm run lint` (0 warnings) and `npm run build` both pass.

---

## 2026-05-31 — Fix: dashboard "Failed to fetch" (trailing-slash 307 → CORS)

- Bug: the Verification Queue showed "Failed to fetch" / 0 reports even though
  the backend `/reports` returned data. `client.ts` called `/reports/` (trailing
  slash) but the backend route is exactly `/reports`. FastAPI answered the slash
  with a **307 redirect to the absolute URL** `http://localhost:8000/reports`.
  In the browser (origin :5173) that cross-origin hop is CORS-blocked, surfacing
  as "Failed to fetch". curl/server-side checks passed because they ignore CORS
  and/or follow the redirect — which is why it looked healthy from the shell.
- Fix: `listReports` now requests `/reports` (no trailing slash) — same-origin
  200, no redirect. Verified from the browser: `fetch('/api/reports')` →
  `200 http://localhost:5173/api/reports`, count=6.
- Note: other client endpoints already use no trailing slash; only the list call
  had one. A more general guard would be `redirect: "error"` in the fetch plus a
  backend `redirect_slashes=False`, but the one-line path fix is the minimal
  change.

---

## 2026-05-31 — Feature: "Check New Company" form on the Verification Queue

- Goal: let an operator submit a new company to verify directly from the queue
  UI, then have the pipeline collect all data it can and produce a report.
- Backend: `POST /submissions` auth changed from `get_current_api_client`
  (X-API-Key only) to `get_principal` (operator Bearer token OR X-API-Key),
  matching the `/reports/{id}/export` precedent. Non-breaking — existing API-key
  integrators still work. `api_client_id` now comes from
  `principal.api_client_id` (None for operator submissions; column already
  nullable). Audit event type/attribution is per-principal
  (`operator.submission_received` vs `system.submission_received`).
- Frontend: new `components/NewCompanyForm.tsx` (modal: 4 required + 5 optional
  fields, empty optionals omitted so URL validation doesn't 422); `client.ts`
  gains `submitCompany()` + `SubmissionRequest`/`SubmissionResponse` types;
  `Dashboard.tsx` gets a "+ Check New Company" button and re-fetches the queue
  on success (refreshKey bump).
- Scope: "find all data" = the existing verification pipeline (registries,
  domain/infra, network/IP, web). No new data sources. In eager mode the run
  completes inline, so the new row appears as `complete` on refresh.
- Gotcha: a running uvicorn does NOT auto-reload on code change — the old
  process kept rejecting operator submissions ("Invalid or missing API key")
  until restarted. Restart the backend after auth changes.
- Tests: added operator-Bearer submit + no-auth-401 cases to
  `tests/test_submissions.py`. Backend 385 passed; frontend lint + 18 tests +
  build all pass.

---

## 2026-09-24 — De-branded for public showcase

- The repo moved to GitHub (`jwolberg/entityiq`) as a portfolio project. All
  references to the originating company were removed from the current tree.
  Docs now describe a generic B2B platform with self-service enterprise
  registration.
- `docs/challenge.md` → `docs/problem-statement.md`, reworded in the project's
  own voice; links updated in README, USERS, ARCHITECTURE, and both build plans.
- Sign-in page: screenshot backdrop replaced with a neutral CSS gradient.
- Web fetcher User-Agent contact URL now points at the GitHub repo.
- Tradeoff (user decision): git history was NOT rewritten. Earlier commits
  still contain the old name and the screenshot asset.

---

## 2026-09-24 — Build-vs-PRD assessment; plan updated

- Full audit in `docs/ASSESSMENT-2026-09-24.md`, including a live stripe.com run
  (SQLite + eager). A legitimate company scored 42 ("review") because MX/SPF
  trust signals are mis-wired, OpenCorporates returns 401 without a token, WHOIS
  never runs (undeclared dependency), and web contacts come back as junk.
- Plan decision: completed Phase-2 tickets keep status Complete (built as
  scoped) and carry a "Gap" annotation pointing at the new Phase 4 fix ticket,
  rather than being reopened. This keeps history honest and the fix work
  trackable.
- Scope added at the user's direction: Phase 5 (showcase readiness: README,
  one-command demo, demo dataset, GitHub CI, optional hosted demo). This
  overrides the plan's "no new scope" update rule.
- Identity-corroboration plan is now sequenced after Phase 4 (it depends on the
  P4-T1 field contract).

---

## 2026-09-24 — P4-T1: real-pipeline e2e test + MX/SPF fix

- Added `backend/tests/pipeline/test_pipeline_e2e.py`. It runs the real stage
  classes in `default_stages()` order with only network clients faked, and a
  guard test asserts the order matches production. RED before the fix:
  `has_mx_records` was missing.
- Fix: the domain adapter now also emits boolean `mx_present` / `spf_present`
  evidence. `mx_records` / `spf_record` stay as the display values the UI reads.
- Deviation from plan: no shared field-constants module. The e2e test catches
  adapter/scoring drift directly without touching ~40 string literals.
- Noticed, not fixed: both `long_lived_domain` (infrastructure) and
  `long_lived_domain_trust` (risk) fire off the same evidence. Double-counting
  may be intended (the layers are separate) — revisit when tuning weights.

---

## 2026-09-24 — P4-T3: live-source viability

- `OPENCORPORATES_API_TOKEN` is read from the environment. The API now returns
  401 without a token, even for dev use.
- The orchestrator detects adapter outages. Adapter stages never raise on
  failure; they return `{"status": <kind>}` under their context key. Any new
  context entry with status `timeout` / `unavailable` / `rate_limited` now
  marks the stage `unavailable`. `not_found` stays `complete`: "no registry
  match" is a finding, not an outage.
- The report's `sources` entries carry `status` (`available` / `unavailable`),
  and unavailable adapter sources are listed with zero evidence. This is a new
  field with a default, so it's backward compatible for API consumers. The UI
  shows an amber "Unavailable during this run" line and counts only available
  sources.
- New runtime dependencies (approved via "move forward with recommendations"):
  `httpx==0.27.2` moved from dev to runtime; `python-whois==0.9.5` added. A
  pyproject test guards against runtime imports living only in the dev extra.

---

## 2026-09-24 — P4-T4: web contact extraction quality

- Test cases come from the live stripe.com junk: `jane.diaz@example.com`,
  `100000000000`, and "100 companies have". The last matched because the
  address regex let "have" end in the "Ave" suffix.
- Emails: placeholder domains and image-asset matches (`hero@2x.png`) are
  dropped. Company-domain addresses sort first. The evidence payload carries
  `on_company_domain`, and `web_contact_email_found` now requires it to be true
  (a vendor's email on the page is not evidence the org runs the site).
- Phones: 10–15 digits, must be formatted (a bare digit run is almost always a
  statistic), no degenerate repeats. Tradeoff: an unformatted real number like
  "4155550142" is now rejected.
- Found while testing: `representation_confidence_signals` returned early when
  there were no field comparisons and skipped web-contact signals, contrary to
  its own comment. Fixed.

---

## 2026-09-24 — P5-T2: one-command demo

- `scripts/demo.sh`: first run creates the venv and runs `npm ci`; every run
  migrates a local SQLite DB, seeds, and serves API + UI with Celery in eager
  mode. Verified: fresh DB → both servers up → lead sign-in through the Vite
  `/api` proxy → Ctrl-C/TERM stops both with no orphan processes.
- `python -m app.seed`: operator + lead accounts (shared demo password) and a
  `demo-integration` API key, printed only on creation. Idempotent.
- Deviation: no docker-compose yet. The Docker daemon wasn't running here to
  verify it, and I won't commit an untested compose file. The script already
  delivers the one-command goal.
- Process note: the seed module was written before its tests were run, so the
  tests never had a RED run. They do assert real behavior (password verifies,
  roles, single key, idempotency).
- Added `backend/.gitignore` (`*.db`) instead of editing the root `.gitignore`,
  which has uncommitted local changes.

---

## 2026-09-24 — P4-T5: review state + notes read path

- `ReportResponse.review` (nullable) holds the latest Review row's status,
  notes, reviewer name, and decided_at. It's on both `GET /reports/{id}` and
  `/export`. It's a new optional field, so it's backward compatible.
- The detail page initializes from `report.review`, so a reviewed company
  reopens as reviewed (no second Mark Reviewed → 409). Notes show in the
  banner.
- Existing behavior surfaced, not changed: `POST /workflow/runs/{id}/notes`
  creates a Review with status "reviewed" if none exists, so adding a note
  marks the run reviewed. The UI now reflects that via an `onNotesSaved`
  callback. Worth revisiting whether notes should imply review.

---

## 2026-09-24 — P4-T6: auth hardening

- `GET /reports` and `GET /reports/{run_id}` now require `get_principal`
  (operator Bearer or API key). The assessment only flagged the detail route,
  but the list was open too, which exposed every company, score, and review
  status. The UI already sent its token, so there's no frontend change for
  this part.
- Existing report-content tests override `get_principal`; auth is covered
  separately in `tests/api/test_report_auth.py`.
- `POST /auth/sign-out` (always 204) invalidates the token. The UI calls it
  best-effort and clears local state regardless.
- Sessions expire after `SESSION_TTL_HOURS` (default 12); expired tokens are
  purged on lookup. The store is still in-memory and single-process: a
  restart signs everyone out, and it won't work across multiple API workers.
  Documented, not changed (Redis-backed store is the noted replacement).

---

## 2026-09-24 — Fix: sanctions hit stored as pre_clear (found building P5-T3)

- Bug: the tier persisted on `RiskAssessment.triage_tier` (dashboard +
  `scores.triage_tier`) came from the score thresholds alone.
  `triage.derive_triage` has a critical-signal override (sanctions hit, high
  ASN reuse → escalate), but only the report's triage section used it. A
  sanctions-matched company with otherwise clean infrastructure showed
  **pre_clear** on the dashboard and **escalate** in its own report.
- Fix: `CRITICAL_ESCALATION_SIGNALS` moved to `engine.py`, and
  `_triage_tier(score, signal_names)` applies it, so both paths use one rule.
  triage.py re-exports the set.
- Existing stored assessments are not recomputed; re-running analysis
  corrects them.

---

## 2026-09-24 — P5-T3: demo dataset

- `app/demo_data.py`: six fictional companies go through the real stage
  classes. Only the network clients are replaced with recorded responses, so
  the demo is deterministic and offline and never contacts real domains.
  Tiers: Northwind + Fabrikam → pre_clear; Contoso (no registry match, foreign
  hosting IP) + Brightpath (young domain, registry outage shown as unavailable)
  → review; Quantum Ledger (3-week shell on a datacenter IP) + Volga Maritime
  (fictional sanctions match) → escalate.
- `tests/test_demo_data.py` pins each scenario's tier, so a scoring change
  that silently re-tiers the demo fails CI.
- All names, domains, and sanctions entries are fictional (the SDN CSV is a
  two-line demo list), so no real company appears as sanctioned.

---

## 2026-09-24 — P2-T9: HQ visualization (Open Decision #4 resolved)

- Decision (applied from the assessment recommendation the user approved):
  OpenStreetMap for the map, Nominatim for geocoding. No API key, and no
  Leaflet: the map is OSM's own embed iframe, so there's no new npm
  dependency. Tradeoff: no custom styling or clustering, and Nominatim's
  policy (identifying UA, ≤1 req/s) rules out bulk use. Swap to a paid
  geocoder at scale.
- New pipeline stage `geocode_hq` after `consistency_checks`, so it can read
  the billing-vs-registry comparison. It geocodes the registry legal address
  when present, else the submitted billing address. Address confidence: high
  (registry + submission agree), medium (registry only), low (self-reported
  or conflicting). Display only; not yet a scoring input.
- Process note: the adapter was written before its tests ran. To compensate,
  I mutated it (confidence + source logic); the tests failed on the mutation
  and passed on restore. Demo scenarios carry recorded coordinates, and a
  live Nominatim lookup was verified once.

---

## 2026-09-24 — P3-T2: audit trail readable + lead gating

- `GET /audit/runs/{run_id}` is open to any signed-in operator: it's the
  history of the case they're viewing. It includes events tied to the run's
  submission (e.g. `submission_received`).
- `GET /audit/events` is lead-only. This is the first real use of
  `require_lead`, which existed but gated nothing. An operator gets 403, and
  the UI hides the nav link for operators, but the API is the enforcement
  point.
- The actor is resolved to the operator email or integration name, else
  "system".
- Activity panel fetches its own data, so an audit-read failure never blocks
  the report page. Two existing CompanyDetail tests that sequence fetch mocks
  by call order needed an audit response inserted: the panel adds a request
  on mount.
- Not done: a pagination cursor (limit ≤ 500 for now) and filtering by actor
  on the server (the UI filters client-side).

---

## 2026-09-24 — P4-T2: dropped signals wired

- Conflicting identities: the resolve stage's `conflict_signal` can never
  fire in production, because its only candidate is synthesized from the
  submission itself. Scoring it would be a no-op, so it stays unwired (the
  deviation from plan). The real signal now comes from the registry adapter:
  ≥2 distinct registered entities (different company numbers) whose names
  equal the submitted name after legal-suffix stripping →
  `registry_identity_conflict` evidence → `conflicting_company_identities`
  (entity, elevated, 0.5). Deliberately narrow, so differently named
  subsidiaries of big companies don't trip it.
- Free email: the list moved from `api/submissions.py` to
  `pipeline/normalize.py` (one source of truth). The normalize stage records
  tier-0 `free_email_domain` evidence (source "submission"). Tier 0 is
  excluded from source-coverage confidence. It's scored as
  `free_email_domain` (representation, elevated, 0.3) on both the normal and
  no-comparison paths.
- Docstrings that claimed `suspicious_dns_infrastructure`,
  `inconsistent_contact_information`, and `ip_distance_flag` now say "not
  implemented".
- `valid_tax_id` stays unreachable; it's handed to identity-corroboration
  IC1-T3.
- Side effect: "submission" appears as a source in the report's sources list
  when free-email evidence exists. Accepted, since it is attributed evidence.

---

## 2026-09-24 — Fix: dashboard showed a sanctions hit as low risk

- Found while taking README screenshots: the queue colored rows by score band
  only and didn't show the triage tier, so Volga Maritime (sanctions hit,
  score 22, tier escalate) appeared as a green "22".
- `GET /reports` items now carry `triage_tier` (new optional field). The
  dashboard adds a Triage badge column, and the score color follows the tier
  when present.
- Not changed: the "risk level" filter still uses score bands, so "High (70+)"
  won't include a sanctions escalation at 22. Follow-up: filter by tier.

---

## 2026-09-24 — Screenshot pass fixes (HQ map, headline tier, demo activity)

- HQ map: replaced the OSM `export/embed.html` iframe with a static 3×3 grid
  of OSM tiles, a centred marker, attribution, and a "View on OpenStreetMap"
  link. Why: the iframe came out blank in headless screenshots, and I couldn't
  confirm it renders anywhere I could observe (the Chrome extension timed out
  3×; I didn't sign in via real Chrome because that means typing a password).
  Plain `<img>` tiles render in any browser, have no iframe dependency, and
  are testable (exact tile URLs asserted). Tradeoff: no pan/zoom; the OSM link
  covers that. OSM tile usage policy (light use, attribution) is respected.
- Detail page headline score is colored by triage tier and names it
  ("Triage: Escalate — … A human decides."). This matches the dashboard fix:
  a sanctions hit with a score of 22 was shown in green.
- Demo dataset records a `system.submission_received` audit event per
  company, so the Activity panel isn't empty in a fresh demo.

---

## 2026-09-24 — Open Decision #7 resolved with defaults (ADR-0002)

- The user asked me to pick a value, so these are defaults and not a legal
  review. Network metadata is kept raw for 90 days, then the IP is truncated
  to /24 and UA and headers are nulled. Submitted PII is kept 5 years after
  review, or 180 days if the submission was never reviewed. Rows are
  anonymized in place, never deleted. The audit log is untouched.
- Every duration can be changed via env var. Revisit them with compliance
  counsel before any real deployment.
- This unblocks P3-T3 / backlog ticket 0002.
