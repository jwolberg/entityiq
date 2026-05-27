# Implementation Notes

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
