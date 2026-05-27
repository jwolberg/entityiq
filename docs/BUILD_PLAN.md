# Build Plan

## Project
- Name: EntityIQ — Enterprise Business Verification & Risk Intelligence Platform
- Summary: When enterprises self-register for SkyFi, operators must decide — quickly
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
- Originating brief: /docs/challenge.md
- UX clarifications: none present (/docs/ux.md absent)

## Planning Assumptions
- **No `/docs/spec.md`.** Per the `plan` skill, the spec role is distributed across
  PRD / STRATEGY / ARCHITECTURE / USERS / challenge; this plan reconciles them.
- **Backend stack planned provisionally as Python + FastAPI** (ARCHITECTURE
  § Open decisions #1 recommendation). Confirmed in P0-T1; all implementation
  tickets depend on it.
- **Job orchestration planned provisionally as Celery + Redis** (Open decision #2).
- **Pipeline sequence reconciliation:** ARCHITECTURE § Agentic verification pipeline
  (9 stages, inserts network/IP enrichment as stage 5) is authoritative for
  technical sequencing over PRD § Agentic Verification Pipeline (9 stages); both
  cover the same work. Conflict-resolution rule #3 applied.
- **Operator auth for the MVP** is assumed session-based with a minimal provider;
  ARCHITECTURE § 5 OIDC/SSO against SkyFi's IdP is the target once the IdP is known.
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
- Current phase: Phase 1 — MVP Vertical Slice (walking skeleton)
- Current ticket: P1-T10 — Operator app: list + detail + mark reviewed
- Phase 0 exit criteria: Met (2026-05-26) — backend stack and orchestration
  confirmed; monorepo + lint/test/CI harness in place; Postgres + migrations +
  core schema runnable (SQLite-verified; Postgres verification pending P1-T1 setup).
- Blockers: Open Decision #5 (data-source licensing for OpenCorporates) remains
  UNRESOLVED. Production use of the OpenCorporates adapter requires a license
  agreement. #4/#7 deferred to their tickets.

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
  - Status: Todo

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
  - Depends on: P1-T3 · AC: ARCHITECTURE § 2 stage 2; PRD pipeline stage 2 · Status: Todo
- P2-T2 — Network/IP intelligence enrichment (IPinfo)
  - Objective: enrich submission IP → geo, ASN/ISP, org, hosting/VPN/proxy,
    distance/mismatch, reuse patterns; emit risk flags.
  - Depends on: P1-T2 · AC: PRD § Network & IP Intelligence (report + flags);
    ARCHITECTURE § 2 stage 5 · Status: Todo
- P2-T3 — Additional Tier-1 sources (gov registries, sanctions/watchlist)
  - Depends on: P1-T4; **Open decision #5** · AC: PRD § Tier 1 (sanctions/registries) · Status: Todo
- P2-T4 — Tier-3 public web evidence (Playwright fallback)
  - Objective: site, contacts, directories, press footprint; contact extraction.
  - Depends on: P1-T3 · AC: PRD § Tier 3; § FE § Contact Information · Status: Todo
- P2-T5 — Adapter robustness: caching, rate-limiting, typed failures, degradation
  - Depends on: P1-T4 · AC: ARCHITECTURE § 4 · Status: Todo

_Track: Risk scoring & explainability_
- P2-T6 — Full four-layer scoring + signal catalog
  - Objective: entity / infrastructure / representation / fraud-staging layers with
    weighting; full elevated-risk and trust signal sets.
  - Depends on: P1-T7, P2-T2 · AC: PRD § Core Verification Philosophy, § Risk
    Signals, § Risk Scoring § Confidence Breakdown · Status: Todo
- P2-T7 — Explainability + triage tiers
  - Objective: per-score evidence + attribution + contributing signals; triage tier
    (pre-clear low-risk vs escalate) feeding the operator queue.
  - Depends on: P2-T6 · AC: PRD § Explainability Requirements; STRATEGY § Our
    approach (triage) + § Key metrics (triage precision/recall) · Status: Todo

_Track: Operator workbench_
- P2-T8 — Detail view completeness
  - Objective: DNS & domain panel, registry info, contact info w/ attribution, risk
    assessment panel (flags + operator notes).
  - Depends on: P1-T10, P2-T6 · AC: PRD § Company Detail View (all subsections) · Status: Todo
- P2-T9 — HQ visualization (map + address confidence)
  - Depends on: P2-T8; **Open decision #4 (map provider)** · AC: PRD § FE § HQ
    Visualization · Status: Todo
- P2-T10 — Full operator actions + dashboard filters
  - Objective: correct submitted data + re-run, re-trigger analysis, add notes,
    export report; dashboard filters/search.
  - Depends on: P1-T10, P2-T11 · AC: PRD § Operator Actions; § Dashboard
    (filters/search) · Status: Todo

_Track: Integration & reporting API_
- P2-T11 — Re-analysis + operator workflow endpoints + report export
  - Depends on: P1-T8 · AC: PRD § Backend § REST API (re-analysis, workflow);
    § API Requirements · Status: Todo
- P2-T12 — API auth for integrating systems
  - Objective: service-credential auth at the API boundary with per-system
    attribution.
  - Depends on: P1-T1 · AC: ARCHITECTURE § 5; USERS § 2 · Status: Todo

### Phase 3 — Hardening & Polish
**Goal**
- Meet performance, audit, privacy, and quality bars; add optional verification.

**Exit Criteria**
- < 2h analysis target met with partial results; full audit history; PII retention
  policy enforced; BE + FE tests and docs complete.

**Tickets**
- P3-T1 — Performance & partial-result UX (< 2h target, timeouts/retries)
  - Depends on: Phase 2 · AC: PRD § Performance Expectations · Status: Todo
- P3-T2 — Auditability completeness + lead audit views
  - Depends on: P1-T9 · AC: PRD § Auditability Requirements; USERS § 3 · Status: Todo
- P3-T3 — PII retention & access policy
  - Depends on: P0-T4; **Open decision #7** · AC: ARCHITECTURE § 6 (PII handling) · Status: Todo
- P3-T4 — Optional domain-ownership verification (email / DNS TXT / HTML meta)
  - Depends on: P1-T1 · AC: PRD § Domain Ownership Verification; USERS § 4 · Status: Todo
- P3-T5 — Test coverage (BE + FE) + documentation
  - Depends on: Phase 2 · AC: PRD § Technical Success, § Code Quality Expectations;
    CLAUDE.md § Validation · Status: Todo

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
25. P2-T9   (Open decision #4)
26. P2-T10
27. P3-T1
28. P3-T2
29. P3-T3   (Open decision #7)
30. P3-T4
31. P3-T5

## Recommended Next Step
- Start with: **P1-T1 — Submission endpoint + network-metadata capture**
- Why this is next: Phase 0 is complete (P0-T1–P0-T4) — stack confirmed, monorepo +
  lint/test/CI in place, and the core data model + migrations landed in P0-T4, which
  P1-T1 depends on. P1-T1 opens the MVP vertical slice: it creates the ingestion
  entry point (submission + network metadata) that every later pipeline, scoring,
  and report ticket builds on.

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
