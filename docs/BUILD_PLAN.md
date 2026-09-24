# Build Plan

## Project
- Name: EntityIQ — Enterprise Business Verification & Risk Intelligence Platform
- Summary: When enterprises self-register on a B2B platform, operators must decide — quickly
  and defensibly — whether a business is real, correctly represented, and safe to
  approve. EntityIQ ingests registration data, runs an authoritative-first,
  multi-source verification pipeline, and produces an explainable, layered risk
  assessment that triages the queue and supports a human verdict (it never
  auto-approves).

## Source of Truth
- What must ship / requirements: /docs/PRD.md
- Approach / scope / metrics / tracks: /docs/STRATEGY.md
- Technical design / open decisions: /docs/ARCHITECTURE.md
- Personas / interfaces: /docs/USERS.md
- Originating brief: /docs/problem-statement.md
- Latest build-vs-PRD audit: /docs/ASSESSMENT-2026-09-24.md (drives Phases 4–5)
- UX clarifications: none present (/docs/ux.md absent)

## Planning Assumptions
- **No `/docs/spec.md`.** Per the `plan` skill, the spec role is distributed across
  PRD / STRATEGY / ARCHITECTURE / USERS / problem statement; this plan reconciles them.
- **Backend stack planned provisionally as Python + FastAPI** (ARCHITECTURE
  § Open decisions #1 recommendation). Confirmed in P0-T1; all implementation
  tickets depend on it.
- **Job orchestration planned provisionally as Celery + Redis** (Open decision #2).
- **Pipeline sequence reconciliation:** ARCHITECTURE § Agentic verification pipeline
  (9 stages, inserts network/IP enrichment as stage 5) is authoritative for
  technical sequencing over PRD § Agentic Verification Pipeline (9 stages); both
  cover the same work. Conflict-resolution rule #3 applied.
- **Operator auth for the MVP** is assumed session-based with a minimal provider;
  ARCHITECTURE § 5 OIDC/SSO against the host organization's IdP is the target once the IdP is known.
- **MVP Tier-1 source assumed to be OpenCorporates** (most accessible structured
  API), pending Open decision #5 on data-source licensing/access.
- **Skill-loader note:** the harness served a stale cached copy of this skill (the
  single-`spec.md` version). This plan was produced from the current on-disk
  multi-doc `plan` skill, which is the intended behavior.

## Architecture Notes
- **Components** (ARCHITECTURE § 1): React/TS operator web app, FastAPI API layer
  (single entry for humans + machines), Redis queue + workers, verification
  pipeline, scoring & explainability engine, Postgres system of record.
- **Pipeline** (ARCHITECTURE § 2): async run per submission; per-stage isolation,
  partial results, retries/timeouts, re-analysis supersedes prior run.
- **Data model** (ARCHITECTURE § 3): `submission`, `entity`, `verification_run`,
  `evidence`, `field_comparison`, `risk_assessment`, `report`, `operator`,
  `review`, `audit_event`.
- **Auth** (ARCHITECTURE § 5): operators/leads via OIDC/SSO + RBAC; integrating
  systems via service credentials; trusted edge IP is authoritative (not raw
  `X-Forwarded-For`).
- **Open Decisions handling:** #1 stack and #2 orchestration are resolved in
  Phase 0 (block all build tickets). #4 map provider blocks P2-T9. #5 data-source
  access blocks/affects P1-T4 and P2-T3. #7 PII retention blocks P3-T3. #6
  co-primary tiebreaker is deferred until a real conflict surfaces.
- **Non-goals affecting implementation** (PRD § Non-Goals): no auto-approval, no
  definitive authorization verification, no full automation of compliance, no fraud
  guarantee. A human signs every approval.

## Current Status
- Overall status: In Progress
- Current phase: Phases 4–5 complete (2026-09-24) except P5-T5 (hosted demo: needs a
  hosting decision) → next: identity-corroboration plan, then Phase 3 remainder
- Current ticket: IC1-T1 (docs/BUILD_PLAN-identity-corroboration.md) — next
- Last completed: P5-T1 — README + screenshots (2026-09-24)
- 2026-09-24 pass: P4-T1..T6, P5-T1..T4, P2-T9, P3-T2 complete, plus fixes found
  along the way (sanctions hit stored as pre_clear; dashboard/detail showed
  escalations in green). Backend 439 tests, frontend 30.
- Phase 2 status: complete except P2-T9 (blocked on Open Decision #4 — map provider)
- Phase 0 exit criteria: Met (2026-05-26) — backend stack and orchestration
  confirmed; monorepo + lint/test/CI harness in place; Postgres + migrations +
  core schema runnable (SQLite-verified; Postgres verification pending P1-T1 setup).
- Phase 1 exit criteria: Met (2026-05-26) — a submitted registration produces a
  stored, viewable report; an operator can sign in (audited), see
  submitted-vs-discovered fields, and mark it reviewed.
- Blockers: Open Decision #5 (data-source licensing) UNRESOLVED. OpenCorporates
  now needs an API token even for dev use (P4-T3 makes it configurable).
  IPINFO_TOKEN production plan unresolved; free tier works. #4 resolved 2026-09-24 (P2-T9 done);
  #7 blocks P3-T3.

---

## Phase Breakdown

### Phase 0 — Decisions & Scaffolding
**Goal**
- Resolve blocking Open Decisions and stand up the monorepo + tooling so build work
  can proceed without re-litigating foundational choices.

**Exit Criteria**
- Backend stack and orchestration confirmed; monorepo
  (`frontend/ backend/ shared/ docs/ tests/`) and lint/test/CI harness in place;
  Postgres + migrations + core schema runnable.

**Tickets**
- P0-T1 — Resolve blocking Open Decisions
  - Objective: Confirm backend stack (rec: Python + FastAPI), job orchestration
    (rec: Celery + Redis), and `shared/` contents (rec: OpenAPI-generated TS types);
    record the choices and rationale.
  - Files likely involved: docs/ARCHITECTURE.md (Open decisions → resolved),
    docs/implementation-notes.md (new)
  - Depends on: —
  - Acceptance criteria covered: ARCHITECTURE § Open decisions #1–#3 resolved;
    unblocks STRATEGY § Tracks
  - Status: Complete (2026-05-26)

- P0-T2 — Monorepo scaffold
  - Objective: Create `frontend/ backend/ shared/ docs/ tests/` with minimal app
    skeletons for the chosen stack.
  - Files likely involved: frontend/, backend/, shared/, tests/
  - Depends on: P0-T1
  - Acceptance criteria covered: PRD § Code Quality Expectations (monorepo);
    PRD § Suggested Architecture § Monorepo; ARCHITECTURE § 1
  - Status: Complete (2026-05-26)

- P0-T3 — Lint, test harness, and CI
  - Objective: Configure linters/formatters and test runners for BE + FE; add
    `.gitlab-ci.yml` running lint + tests.
  - Files likely involved: backend/ (pyproject/ruff/pytest), frontend/
    (eslint/vitest), .gitlab-ci.yml
  - Depends on: P0-T2
  - Acceptance criteria covered: PRD § Technical Success (test coverage);
    CLAUDE.md § Validation
  - Status: Complete (2026-05-26)

- P0-T4 — Postgres + migrations + core data model
  - Objective: Stand up Postgres and migration tooling; implement the core tables
    (`submission`, `entity`, `verification_run`, `evidence`, `report`,
    `risk_assessment`, `operator`, `review`, `audit_event`).
  - Files likely involved: backend/models, backend/migrations
  - Depends on: P0-T2, P0-T3
  - Acceptance criteria covered: ARCHITECTURE § 3 Data model;
    PRD § Auditability Requirements (audit_event)
  - Status: Complete (2026-05-26)

### Phase 1 — MVP Vertical Slice (walking skeleton)
**Goal**
- Thinnest end-to-end path through both co-primary surfaces: a submitted
  registration is verified by a minimal pipeline, scored, stored, and reviewable by
  an authenticated operator.

**Exit Criteria**
- A submitted registration produces a stored, viewable report; an operator can sign
  in (audited), see submitted-vs-discovered fields, and mark it reviewed.

**Tickets**
- P1-T1 — Submission endpoint + network-metadata capture
  - Objective: `POST` submission accepting required + optional inputs; capture
    network metadata server-side (source IP, user agent, timestamp, forwarded
    headers, endpoint), trusting the edge-assigned IP; persist `submission` and
    enqueue a `verification_run`.
  - Files likely involved: backend/api, backend/models
  - Depends on: P0-T4
  - Acceptance criteria covered: PRD § Backend § REST API (submission endpoint),
    § API § Submission Endpoint, § Inputs, § Network & IP Intelligence (captured
    fields); ARCHITECTURE § 5 (trusted edge IP)
  - Status: Complete (2026-05-26)

- P1-T2 — Async run orchestration skeleton
  - Objective: Queue + worker that drives a `verification_run` through pipeline
    stages with status per stage, partial-result visibility, and re-analysis
    superseding the prior run.
  - Files likely involved: backend/pipeline, backend/queue
  - Depends on: P1-T1, P0-T1 (orchestration choice)
  - Acceptance criteria covered: ARCHITECTURE § 2 (execution properties);
    PRD § Performance Expectations (partial results viewable)
  - Status: Complete (2026-05-26)

- P1-T3 — Pipeline stage: normalize input
  - Objective: Canonicalize domain, country, address, email; stub country-aware
    tax-ID formatting.
  - Files likely involved: backend/pipeline/normalize
  - Depends on: P1-T2
  - Acceptance criteria covered: ARCHITECTURE § 2 stage 1; PRD § Agentic Pipeline
    stage 1
  - Status: Complete (2026-05-26)

- P1-T4 — Tier-1 authoritative source adapter (OpenCorporates)
  - Objective: Implement the adapter interface and one Tier-1 lookup returning
    normalized `evidence` with source attribution + confidence; typed failures and
    graceful degradation.
  - Files likely involved: backend/adapters/opencorporates, backend/adapters/base
  - Depends on: P1-T3; **Open decision #5 (data-source access/licensing)**
  - Acceptance criteria covered: PRD § Verification Sources § Tier 1;
    ARCHITECTURE § 4 (adapter contract)
  - Status: Complete (2026-05-26) — Open Decision #5 still UNRESOLVED; module
    docstring warns against production use without a license.

- P1-T5 — Tier-2 domain/infrastructure signals
  - Objective: WHOIS/domain age, DNS, MX, SPF/DKIM, SSL metadata → `evidence`.
  - Files likely involved: backend/adapters/domain
  - Depends on: P1-T3
  - Acceptance criteria covered: PRD § Verification Sources § Tier 2;
    PRD § FE § DNS & Domain Intelligence
  - Status: Complete (2026-05-26)

- P1-T6 — Consistency checks → field comparisons
  - Objective: Compute submitted-vs-discovered `field_comparison` (match / mismatch
    / unverified) for core fields.
  - Files likely involved: backend/pipeline/consistency, backend/models
  - Depends on: P1-T4, P1-T5
  - Acceptance criteria covered: PRD § FE § Registration Data (match/mismatch);
    ARCHITECTURE § 2 stage 7
  - Status: Complete (2026-05-26)

- P1-T7 — Minimal risk assessment + report assembly
  - Objective: Produce overall 0–100 score and the available layer scores from
    current signals, each linked to `evidence`; assemble a queryable `report`.
  - Files likely involved: backend/scoring, backend/report, backend/models
  - Depends on: P1-T6
  - Acceptance criteria covered: PRD § Risk Scoring, § Explainability Requirements;
    ARCHITECTURE § 2 stage 8
  - Status: Complete (2026-05-26)

- P1-T8 — Report retrieval API
  - Objective: `GET` report returning normalized report, evidence, scores,
    mismatches, sources; partial-result aware.
  - Files likely involved: backend/api
  - Depends on: P1-T7
  - Acceptance criteria covered: PRD § API § Report Endpoint; § Backend § REST API
    (report retrieval)
  - Status: Complete (2026-05-26)

- P1-T9 — Operator auth + audit foundation
  - Objective: Operator sign-in (minimal session for MVP), RBAC (operator / lead),
    and append-only `audit_event` on every operator action.
  - Files likely involved: backend/auth, frontend/auth, backend/models
  - Depends on: P0-T4
  - Acceptance criteria covered: PRD § FE § Operator Authentication;
    § Auditability Requirements; USERS § Access, permissions & audit;
    ARCHITECTURE § 5
  - Status: Complete (2026-05-26)

- P1-T10 — Operator app: list + detail + mark reviewed
  - Objective: Dashboard list (company, analysis date, risk score, review status);
    detail view showing submitted-vs-discovered with match/mismatch indicators;
    mark-reviewed action (audited).
  - Files likely involved: frontend/
  - Depends on: P1-T8, P1-T9
  - Acceptance criteria covered: PRD § FE § Dashboard, § Company Detail §
    Registration Data, § Operator Actions (mark reviewed); USERS § 1
  - Status: Complete (2026-05-26)

### Phase 2 — Deepen the Tracks
**Goal**
- Expand each STRATEGY Track from the MVP slice to full PRD scope.

**Exit Criteria**
- All four tiers of evidence, the full four-layer explainable scoring with triage
  tiers, the full operator workbench, and the full integration/reporting API are in
  place.

**Tickets** (grouped by STRATEGY § Tracks)

_Track: Evidence & enrichment pipeline_
- P2-T1 — Entity candidate resolution (pipeline stage 2)
  - Depends on: P1-T3 · AC: ARCHITECTURE § 2 stage 2; PRD pipeline stage 2 · Status: Complete (2026-05-27)
- P2-T2 — Network/IP intelligence enrichment (IPinfo)
  - Objective: enrich submission IP → geo, ASN/ISP, org, hosting/VPN/proxy,
    distance/mismatch, reuse patterns; emit risk flags.
  - Depends on: P1-T2 · AC: PRD § Network & IP Intelligence (report + flags);
    ARCHITECTURE § 2 stage 5 · Status: Complete (2026-05-27)
    · Gap (2026-09-24): `ip_distance_flag` is documented but not implemented; geography is country-equality only → P4-T2.
- P2-T3 — Additional Tier-1 sources (gov registries, sanctions/watchlist)
  - Depends on: P1-T4; **Open decision #5** · AC: PRD § Tier 1 (sanctions/registries) · Status: Complete (2026-05-27) — OFAC SDN screening implemented; gov registries deferred per Open Decision #5.
- P2-T4 — Tier-3 public web evidence (Playwright fallback)
  - Objective: site, contacts, directories, press footprint; contact extraction.
  - Depends on: P1-T3 · AC: PRD § Tier 3; § FE § Contact Information · Status: Complete (2026-05-27) — httpx fetcher + lazy Playwright; contacts (email/phone/address) + branding + footprint signals.
- P2-T5 — Adapter robustness: caching, rate-limiting, typed failures, degradation
  - Depends on: P1-T4 · AC: ARCHITECTURE § 4 · Status: Complete (2026-05-27) — AdapterCache (per-source TTL, FIFO eviction), RateLimiter (token bucket + backoff), SourceAvailabilityTracker, CachedAdapter + RateLimitedAdapter wrappers.

_Track: Risk scoring & explainability_
- P2-T6 — Full four-layer scoring + signal catalog
  - Objective: entity / infrastructure / representation / fraud-staging layers with
    weighting; full elevated-risk and trust signal sets.
  - Depends on: P1-T7, P2-T2 · AC: PRD § Core Verification Philosophy, § Risk
    Signals, § Risk Scoring § Confidence Breakdown · Status: Complete (2026-05-27) — full PRD signal catalog in signals.py; cross-submission IP/ASN reuse implemented; 332 tests pass.
    · Gaps (2026-09-24): MX/SPF trust signals are never triggered (field-name mismatch) → P4-T1;
      conflict_signal, free-email, and valid_tax_id are not scored → P4-T2.
- P2-T7 — Explainability + triage tiers
  - Objective: per-score evidence + attribution + contributing signals; triage tier
    (pre-clear low-risk vs escalate) feeding the operator queue.
  - Depends on: P2-T6 · AC: PRD § Explainability Requirements; STRATEGY § Our
    approach (triage) + § Key metrics (triage precision/recall) · Status: Complete (2026-05-27) — triage.py + explain.py; triage + explainability in report summary; 356 tests pass; no auto-approval.

_Track: Operator workbench_
- P2-T8 — Detail view completeness
  - Objective: DNS & domain panel, registry info, contact info w/ attribution, risk
    assessment panel (flags + operator notes).
  - Depends on: P1-T10, P2-T6 · AC: PRD § Company Detail View (all subsections) · Status: Complete (2026-05-31) — frontend-only; DomainPanel/RegistryPanel/ContactPanel (w/ source attribution)/RiskAssessmentPanel in components/DetailPanels.tsx, driven by existing report API; operator-notes textarea added to Mark-Reviewed; lint clean, 12 FE tests pass.
    · Gap (2026-09-24): saved notes and review state are never read back → P4-T5.
- P2-T9 — HQ visualization (map + address confidence)
  - Depends on: P2-T8; **Open decision #4 (map provider)** · AC: PRD § FE § HQ
    Visualization · Status: Complete (2026-09-24) — Open Decision #4 resolved (OSM embed +
    Nominatim). New `geocode_hq` stage (after consistency) emits coordinates + address
    confidence (high: registry+submission agree / medium: registry only / low: self-reported
    or conflicting); HqPanel on the detail page; live Nominatim lookup verified.
- P2-T10 — Full operator actions + dashboard filters
  - Objective: correct submitted data + re-run, re-trigger analysis, add notes,
    export report; dashboard filters/search.
  - Depends on: P1-T10, P2-T11 · AC: PRD § Operator Actions; § Dashboard
    (filters/search) · Status: Complete (2026-05-31) — frontend-only; new
    components/OperatorActions.tsx wires the P2-T11 endpoints (re-run, correct +
    re-run [sends only changed fields], add notes, export JSON); Dashboard gains
    search + review-status + risk-band filters. lint clean, 18 FE tests pass.
    · Gap (2026-09-24): reopening a reviewed run shows it unreviewed and returns a 409 → P4-T5.

_Track: Integration & reporting API_
- P2-T11 — Re-analysis + operator workflow endpoints + report export
  - Depends on: P1-T8 · AC: PRD § Backend § REST API (re-analysis, workflow);
    § API Requirements · Status: Complete (2026-05-27) — done out of order; endpoints:
    POST /reanalysis, POST /workflow (correct+re-run, notes), report export.
- P2-T12 — API auth for integrating systems
  - Objective: service-credential auth at the API boundary with per-system
    attribution.
  - Depends on: P1-T1 · AC: ARCHITECTURE § 5; USERS § 2 · Status: Complete (2026-05-31) —
    backend-only; ApiClient service-credential table (keys hashed at rest, X-API-Key
    header); POST /submissions now requires a key; report export accepts a Principal
    (system key OR operator token); per-system attribution via submission.api_client_id
    + audit events (system.submission_received, report.exported). 383 tests pass;
    migration verified up/down on SQLite. Follow-ups: mTLS, key-provisioning UI,
    locking operator-only report reads.

### Phase 3 — Hardening & Polish
**Goal**
- Meet performance, audit, privacy, and quality bars; add optional verification.

**Exit Criteria**
- < 2h analysis target met with partial results; full audit history; PII retention
  policy enforced; BE + FE tests and docs complete.

**Tickets**
- P3-T1 — Performance & partial-result UX (< 2h target, timeouts/retries)
  - Objective: record and expose run duration (`finished_at - started_at`);
    add a Celery soft time limit and per-stage timeout; retry transient adapter
    failures.
  - Depends on: Phase 2 · AC: PRD § Performance Expectations · Status: Complete (2026-09-24) —
    per-stage timeout with isolated stage sessions (late writes discarded); bounded
    retries via RetryingAdapter; run budget (skip sources, always score + store);
    Celery soft/hard limits keep a partial report; run timing + stage progress on
    GET /reports/{id}; detail page live-refreshes in-flight runs. Ticket 0001.
- P3-T2 — Auditability completeness + lead audit views
  - Objective: `GET` audit-events API (per run and global), an Activity panel on
    the company detail page, and a lead-only audit view gated by the currently
    unused `require_lead`.
  - Depends on: P1-T9 · AC: PRD § Auditability Requirements; USERS § 3 · Status: Complete (2026-09-24) —
    `GET /audit/runs/{id}` (any operator; run + its submission, oldest first) and
    `GET /audit/events` (lead-only via `require_lead`, newest first, filterable); Activity
    timeline on the detail page; lead-only Audit Log page + nav link.
- P3-T3 — PII retention & access policy
  - Depends on: P0-T4; **Open decision #7** · AC: ARCHITECTURE § 6 (PII handling) · Status: Todo
- P3-T4 — Optional domain-ownership verification (email / DNS TXT / HTML meta)
  - Depends on: P1-T1 · AC: PRD § Domain Ownership Verification; USERS § 4 · Status: Todo
- P3-T5 — Test coverage (BE + FE) + documentation
  - Depends on: Phase 2 · AC: PRD § Technical Success, § Code Quality Expectations;
    CLAUDE.md § Validation · Status: Todo

---

### Phase 4 — Real-data correctness (from 2026-09-24 assessment)
**Goal**
- Make scores and evidence trustworthy on real companies, not just on test
  fixtures. Fixes gaps inside already-scoped PRD requirements; adds no scope.

**Exit Criteria**
- An end-to-end test drives `default_stages()` with fake adapters and asserts
  that the expected signals fire.
- A live run on a well-known legitimate company (e.g. stripe.com) lands in
  `pre_clear` with registry, domain-age, MX, and SPF evidence and no junk
  contacts.

**Tickets**
- P4-T1 — Real-pipeline integration test + evidence field contract
  - Objective: add an integration test running the real stage list with
    injected fake clients. Fix the MX/SPF field mismatch (adapter emits
    `mx_records`/`spf_record`; scoring reads `mx_present`/`spf_present`). Define
    the evidence field names as shared constants so adapters and signals can't
    drift apart.
  - Files likely involved: backend/app/adapters/domain.py, backend/app/scoring/signals.py,
    backend/tests/pipeline/test_pipeline_e2e.py (new)
  - Depends on: — · AC: PRD § Tier 2; § Trust Signals · Status: Complete (2026-09-24) —
    tests/pipeline/test_pipeline_e2e.py drives the real stage list with faked clients;
    domain adapter now emits boolean `mx_present`/`spf_present`. Shared-constants
    module not added: the e2e test catches drift more cheaply.
- P4-T2 — Wire computed-but-dropped signals; remove phantom claims
  - Objective: persist resolve-stage `conflict_signal` as evidence and score it
    ("multiple conflicting identities"). Persist free/disposable email as
    evidence and score it. Implement or delete docstring-only signals
    (`suspicious_dns_infrastructure`, `inconsistent_contact_information`,
    `ip_distance_flag`). Leave `valid_tax_id` to identity-corroboration IC1-T3.
  - Depends on: P4-T1 · AC: PRD § Elevated Risk Indicators; § Inputs · Status: Complete (2026-09-24) —
    `conflicting_company_identities` (registry: ≥2 distinct entities with exactly the submitted
    name) and `free_email_domain` (tier-0 intake evidence) are scored; phantom docstring claims
    corrected to "not implemented". Deviation: resolve-stage `conflict_signal` left unwired —
    its only candidate is synthesized from the submission, so it can never fire.
- P4-T3 — Live-source viability + runtime dependencies
  - Objective: add an `OPENCORPORATES_API_TOKEN` env var. Surface adapter
    failures (401, unavailable) in the report's `sources` instead of dropping
    them silently. Move `httpx` to the main dependencies, declare the WHOIS
    library, and verify WHOIS domain age works live. Document every key in
    RUNBOOK.
  - Depends on: P4-T1; Open Decision #5 for *production* use only · AC: PRD § Tier 1–2;
    ARCHITECTURE § 4 · Status: Complete (2026-09-24) — env token; outage
    kinds (timeout/unavailable/rate_limited) mark the stage `unavailable` and the source
    is listed with `status: unavailable` in the report + UI; httpx and python-whois
    are runtime deps; WHOIS verified live (stripe.com → 1995).
- P4-T4 — Web contact extraction quality
  - Objective: reject placeholder emails (example.com etc.), invalid phone
    numbers, and address false positives. Add fixtures from real saved pages.
    Only give `web_contact_email_found` trust credit for emails on the
    company's own domain.
  - Depends on: — · AC: PRD § Tier 3; FE § Contact Information · Status: Complete (2026-09-24) —
    placeholder/asset emails dropped, company-domain emails first + `on_company_domain`
    flag gates the trust signal; phones need 10–15 digits and formatting; address suffix
    must be its own word. Cases taken from the live stripe.com run.
- P4-T5 — Review state + notes read path
  - Objective: include review status, reviewer, and notes in `ReportResponse`.
    Show them on the company detail page. Hide or disable Mark Reviewed once a
    run is reviewed, so there's no 409 on reopen.
  - Depends on: — · AC: PRD § Operator Actions; FE § Risk Assessment (operator notes) · Status: Complete (2026-09-24) —
    `review` (status, notes, reviewer, decided_at) on report + export responses; detail page
    loads it, shows notes, hides Mark Reviewed once reviewed; notes added via Operator
    Actions update the banner.
- P4-T6 — Auth hardening
  - Objective: require an operator token or API key on `GET /reports/{run_id}`.
    Make sign-out call the server to invalidate the session. Add a session TTL.
  - Depends on: — · AC: ARCHITECTURE § 5; PRD § Operator Authentication · Status: Complete (2026-09-24) —
    `GET /reports` AND `GET /reports/{id}` now require an operator token or API key (the list
    was also open, not just the detail); `POST /auth/sign-out` + UI calls it; 12h session TTL.

### Phase 5 — Showcase readiness
**Goal**
- Let an evaluator clone the repo (or open a link) and see a convincing,
  working product in minutes. Scope added 2026-09-24 at the user's direction
  (portfolio use); it serves PRD § Technical Success ("strong documentation").

**Exit Criteria**
- One command brings up API + UI with seeded demo data spanning all three
  triage tiers. GitHub CI is green. The README explains the product with
  screenshots.

**Tickets**
- P5-T1 — README rewrite
  - Objective: what it is, why it's interesting (deterministic, explainable,
    human-in-the-loop), architecture diagram, screenshots, quick start, status,
    and links to PRD / ARCHITECTURE / ASSESSMENT.
  - Depends on: P5-T2 (for screenshots) · Status: Complete (2026-09-24) — README rewritten with
    quick start, capability table, pipeline diagram, quality, honest known gaps; screenshots
    of the queue, a sanctions-escalation detail page, HQ map, and lead audit log captured
    from the demo. Screenshot pass surfaced + fixed 2 UI bugs (tier not shown; blank map).
- P5-T2 — One-command local demo
  - Objective: seed script (operator + lead accounts, API key) and
    docker-compose (API + UI; SQLite + eager by default, Postgres + Redis
    profile). Replaces the inline Python snippets in RUNBOOK.
  - Depends on: — · Status: Complete (2026-09-24) — `scripts/demo.sh` (no Docker:
    venv + npm install on first run, SQLite + eager, seed, both servers) and idempotent
    `python -m app.seed`. docker-compose deferred: Docker daemon unavailable to verify it,
    and the script already meets the one-command goal.
- P5-T3 — Demo dataset
  - Objective: curated submissions that land in `pre_clear`, `review`, and
    `escalate` (including a sanctions hit and a fresh-domain shell). They use
    recorded adapter responses so the demo is deterministic and works offline,
    with realistic source IPs so the network layer shows up.
  - Depends on: P4-T1, P5-T2 · Status: Complete (2026-09-24) — `app/demo_data.py`: 6 fictional
    companies (2 pre_clear, 2 review, 2 escalate incl. a fictional sanctions match) through
    the real stage list with recorded responses; tiers pinned by tests; loaded by demo.sh.
    Surfaced and fixed the sanctions→pre_clear tier bug.
- P5-T4 — GitHub Actions CI
  - Objective: port `.gitlab-ci.yml` (ruff, pytest, eslint, vitest, tsc) to
    `.github/workflows/`. Remove the GitLab config and the untracked bun
    template.
  - Depends on: — · Status: Complete (2026-09-24) — backend (ruff, format,
    pytest) + frontend (eslint, vitest, tsc + vite build) jobs; `.gitlab-ci.yml` removed.
    First real run happens when the PR is pushed.
- P5-T5 — Hosted demo (optional)
  - Objective: deploy a read-mostly demo instance with seeded data and a
    shared demo login.
  - Depends on: P5-T2, P5-T3, P4-T6; **needs a hosting decision** · Status: Todo

---

## Dependency Order
1. P0-T1
2. P0-T2
3. P0-T3
4. P0-T4
5. P1-T1
6. P1-T2
7. P1-T3
8. P1-T5
9. P1-T4   (confirm Open decision #5 first)
10. P1-T6
11. P1-T7
12. P1-T8
13. P1-T9
14. P1-T10  ← MVP vertical slice complete
15. P2-T1
16. P2-T2
17. P2-T5
18. P2-T3   (Open decision #5)
19. P2-T4
20. P2-T6
21. P2-T7
22. P2-T11
23. P2-T12
24. P2-T8
25. P2-T9   (rescheduled at 35)
26. P2-T10
27. P4-T1   ← start here (2026-09-24 assessment)
28. P4-T3
29. P4-T4
30. P5-T4
31. P5-T2
32. P4-T5
33. P4-T6
34. P5-T3
35. P2-T9   (Open decision #4 resolved: OSM embed + Nominatim)
36. P3-T2
37. P5-T1
38. P4-T2
39. Identity corroboration plan (docs/BUILD_PLAN-identity-corroboration.md)
40. P3-T1
41. P3-T4
42. P3-T3   (Open decision #7)
43. P3-T5
44. P5-T5   (hosting decision)

## Recommended Next Step
- Start with: **IC1-T1 — Tax-ID adapter scaffold + stub provider**
  (docs/BUILD_PLAN-identity-corroboration.md). Its prerequisite P4-T1 is done.
- Why: Phases 4–5 made the existing product correct on real data and presentable.
  The largest remaining PRD gap is the representation layer: tax ID and LinkedIn are
  captured but never verified, and `valid_tax_id` is unreachable.
- Then: P3-T1 (run timing, stage timeouts), P3-T4 (domain-ownership verification),
  P3-T3 (PII policy; Open Decision #7), P3-T5. Follow-ups noted in implementation
  notes: triage-tier dashboard filter, Redis-backed sessions, docker-compose,
  API-key provisioning UI.
- Needs a human decision: P5-T5 hosting; OpenCorporates token/license (Open Decision #5).

## Deferred / Out of Scope
- **Auto-approval / full automation of compliance decisions** — PRD § Non-Goals
  (a human signs every approval).
- **Definitive legal authorization verification** and **fraud-prevention
  guarantees** — PRD § Non-Goals; domain-ownership only raises confidence.
- **Co-primary user tiebreaker** (Open decision #6) — deferred until operator-UX and
  API-consumer needs actually conflict.
- **LinkedIn / aggressive scraping** — gated on Open decision #5 (ToS/licensing);
  PRD § Known Challenges (scraping brittleness).
- **HQ map provider, PII retention policy** — parked as Open decisions #4 / #7 until
  their tickets (P2-T9 / P3-T3).

## Update Rules
After each implementation pass:
- Update ticket status only as Todo / In Progress / Complete / Blocked
- Update Current Status (phase, ticket, blockers — including any Open decision a
  ticket is blocked on)
- Set the next recommended ticket
- Do NOT add new scope unless a REQUIRED input document changes
