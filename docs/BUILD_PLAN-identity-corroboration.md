# Build Plan — External Identity Corroboration (FEIN + LinkedIn)

> **Feature-scoped plan.** This plans only the feature in
> [PRD-identity-corroboration.md](./PRD-identity-corroboration.md). The
> whole-product plan lives in [BUILD_PLAN.md](./BUILD_PLAN.md) and is left
> untouched. Tickets are prefixed **`IC-`** to avoid collision with the product
> plan's `P-` tickets.

## Project
- Name: EntityIQ — External Identity Corroboration (Government/Tax-ID + LinkedIn)
- Summary: Add two new verification sources — an authoritative tax-ID/FEIN check
  (Tier 1) and a LinkedIn presence check (Tier 3) — that verify two inputs the
  platform already collects but never corroborates, strengthening the **Entity
  Legitimacy** and **Representation Confidence** scoring layers.

## Source of Truth
- Feature requirements: /docs/PRD-identity-corroboration.md
- What must ship / parent requirements: /docs/PRD.md
- Approach / scope / metrics / tracks: /docs/STRATEGY.md
- Technical design / open decisions: /docs/ARCHITECTURE.md
- Personas / interfaces: /docs/USERS.md
- Originating brief: /docs/problem-statement.md
- UX clarifications: none present (/docs/ux.md absent)

## Planning Assumptions
- **Feature PRD is authoritative for this plan**; it extends and defers to the
  parent PRD. Where the feature PRD is silent, parent docs govern (conflict order:
  PRD → STRATEGY → ARCHITECTURE → USERS → problem statement).
- **Base platform is already built** (product BUILD_PLAN.md: Phase 2 complete).
  The adapter contract, evidence / `field_comparison` / `risk_assessment` data
  model, four-layer scoring, operator detail panels, and report/export API all
  exist. This plan only *adds adapters and wiring*; it assumes no scaffolding work.
- **Ticket prefix `IC-`** distinguishes this feature from the product plan's
  `P-` tickets (planning convenience; not a code namespace).
- **`IC-` phases adapt the skill's MVP-first model to an extension feature:**
  Phase 0 = blocking provider decisions; Phase 1 = end-to-end vertical slice
  **behind stubbed providers** (proves integration with zero vendor dependency,
  per feature PRD §14 Phase 1); Phase 2/3 = wire the real FEIN then LinkedIn
  providers; Phase 4 = hardening. This is the narrowest path to working, observable
  behavior before any licensing is cleared.
- **No new DB tables and no new API endpoints** (feature PRD §8, §10) — both
  features reuse `evidence`, `field_comparison`, and `risk_assessment.contributing_signals`.
- **Provider selection is treated as ARCHITECTURE Open Decision #5** (data-source
  access — *explicitly names LinkedIn*). It is **UNRESOLVED**; planned per the Open
  Decisions handling rule: decisions are their own Phase-0 tickets, and the live
  wiring tickets (IC2-T1, IC3-T1) are **Blocked** on them.

## Architecture Notes
- **Adapter contract** (ARCHITECTURE § 4): `fetch(entity_context) -> [evidence]`
  with typed failures (timeout / unavailable / not-found / rate-limited), per-source
  caching keyed by `(source, lookup_key)`, rate-limiting, and graceful degradation.
  Both new sources implement this verbatim.
- **Pipeline** (ARCHITECTURE § 2): new stages register in
  `backend/app/pipeline/orchestrator.py` `default_stages()`. FEIN joins the Tier-1
  group (stage 3); LinkedIn joins the Tier-3 group (stage 6). Per-stage isolation +
  partial results already enforced by the orchestrator.
- **Scoring** (PRD § Core Verification Philosophy): FEIN → **Entity Legitimacy**;
  LinkedIn → **Representation Confidence** (primary) + **Entity Legitimacy**
  (secondary). New signals added to the existing signal catalog
  (`backend/app/scoring/`); explainability is automatic via `contributing_signals`.
- **Data model** (ARCHITECTURE § 3): reuse `evidence` (new `source` values
  `tax_id`, `linkedin`), `field_comparison` (new rows), `risk_assessment`
  (new signals), `audit_event` (sources-used already covered).
- **Operator surface** (USERS § 1): extends the existing Company Detail panels
  (`frontend/src/components/DetailPanels.tsx`, `RegistrationDiff.tsx`); no new page.
- **Open Decisions handling:** #5 (data-source access) → IC0-T1 (FEIN) and IC0-T2
  (LinkedIn); blocks IC2-T1 / IC3-T1. #7 (PII retention) → affects IC4-T3
  (requester-association data is PII). No other open decisions apply.
- **Non-goals affecting implementation** (feature PRD §3, §15): no authorization
  proofing, no individual KYC, no LinkedIn scraping, no non-US tax jurisdictions in
  v1, no new operator actions or API resources.

## Current Status
- Overall status: In Progress
- Current phase: Phase 1 complete (2026-09-24, backlog 0007–0013); Phase 0 decisions
  open; Phases 2–3 blocked on them
- Current ticket: IC0-T1 / IC0-T2 (provider decisions, backlog 0005 / 0006)
- Phase 1 notes: both adapters default to an *unconfigured* provider (source
  unavailable, no signal, no penalty); ENTITYIQ_TAX_ID_PROVIDER / _LINKEDIN_PROVIDER
  = stub enables the deterministic stubs. valid_tax_id was replaced by
  tax_id_verified_active. See implementation-notes 2026-09-24.
- Blockers: Open Decision #5 (data-source access for FEIN provider + LinkedIn
  data access / ToS) UNRESOLVED — blocks IC2-T1 and IC3-T1 only. The Phase-1
  stubbed slice is **not** blocked.
- Sequencing (2026-09-24): scheduled **after** main BUILD_PLAN Phase 4
  (real-data correctness). The 2026-09-24 assessment
  (docs/ASSESSMENT-2026-09-24.md) found adapter evidence field names drifting
  from what scoring reads (MX/SPF). IC1-T1 and IC1-T4 should build on the
  shared field constants and end-to-end pipeline test from P4-T1, so the new
  tax-ID/LinkedIn signals can't repeat that drift.
- Related finding: the existing `valid_tax_id` trust signal
  (backend/app/scoring/signals.py) is unreachable today because no adapter
  emits `tax_id` evidence. IC1-T3 should replace it with the `tax_id_*`
  signals rather than add alongside it. `requester_full_name` and
  `linkedin_url` are captured but unused, which is the gap IC1-T4/T5 close.

---

## Phase Breakdown

### Phase 0 — Provider Decisions (blocking for live wiring only)
**Goal**
- Resolve the two data-source access decisions that gate live verification.

**Exit Criteria**
- A selected FEIN-verification provider and a selected LinkedIn data-access path,
  each recorded with rationale and ToS/licensing/cost cleared.

**Tickets**
- IC0-T1 — Decide FEIN / tax-ID verification provider
  - Objective: Choose among commercial KYB name/TIN match (rec.), IRS TIN Matching
    e-Services, or state SoS registries; clear cost + ToS; record the decision.
  - Files likely involved: docs/ARCHITECTURE.md (Open Decision #5),
    docs/implementation-notes.md
  - Depends on: —
  - Acceptance criteria covered: feature PRD §5 (Provider — Open Decision);
    ARCHITECTURE § Open Decisions #5
  - Status: Todo

- IC0-T2 — Decide LinkedIn data-access path + legal sign-off
  - Objective: Choose official LinkedIn API vs. licensed third-party provider
    (rec.); confirm scraping is excluded; obtain legal sign-off; record the decision.
  - Files likely involved: docs/ARCHITECTURE.md (Open Decision #5),
    docs/implementation-notes.md
  - Depends on: —
  - Acceptance criteria covered: feature PRD §6 (Provider — Open Decision), §13
    (Risks 1–2); ARCHITECTURE § Open Decisions #5; PRD § Known Challenges (scraping)
  - Status: Todo

### Phase 1 — Vertical Slice Behind Stubbed Providers
**Goal**
- Prove the full end-to-end integration (adapter → evidence → field comparison →
  scoring → operator UI + export) for both features using deterministic fixtures,
  with **no vendor dependency**.

**Exit Criteria**
- A submission flows through both new stubbed adapters and produces: a real Tax-ID
  diff row, a LinkedIn corroboration panel, and new trust/elevated signals on the
  Entity and Representation layers — all visible in the operator UI and in
  `/reports/{id}/export`, with correct `pending` / `unavailable` / populated states.

**Tickets**
- IC1-T1 — Tax-ID adapter scaffold + stub provider (Tier 1)
  - Objective: Implement `verify_tax_id` adapter (class `name = "tax_id"`,
    `tier = 1`) behind the adapter contract; deterministic stub returns
    `tax_id_status` / `tax_id_registered_name` / `tax_id_name_match` evidence;
    typed failures; register in `default_stages()` Tier-1 group; non-US →
    `unavailable`.
  - Files likely involved: backend/app/adapters/tax_id.py (new),
    backend/app/adapters/base.py, backend/app/pipeline/orchestrator.py,
    backend/tests/
  - Depends on: — (base platform exists)
  - Acceptance criteria covered: feature PRD §5 (Behavior, Evidence emitted);
    ARCHITECTURE § 4 (adapter contract), § 2 stage 3
  - Status: Complete (2026-09-24)

- IC1-T2 — Tax-ID consistency / field comparisons
  - Objective: Upgrade the **Tax ID** comparison from permanent `unverified` to
    `match`/`mismatch`/`unverified`; add a **Registered Name (tax ID)** comparison
    vs. submitted `company_name`.
  - Files likely involved: backend/app/pipeline/consistency.py,
    backend/app/models (field_comparison), backend/tests/
  - Depends on: IC1-T1
  - Acceptance criteria covered: feature PRD §5 (Field comparison); PRD § FE
    § Registration Data (match/mismatch); ARCHITECTURE § 2 stage 7
  - Status: Complete (2026-09-24)

- IC1-T3 — Tax-ID scoring signals (Entity Legitimacy)
  - Objective: Add `tax_id_verified_active` (trust) and `tax_id_not_found` /
    `tax_id_name_mismatch` / `tax_id_inactive_or_dissolved` (elevated) to the
    signal catalog on the entity layer; `unavailable` contributes no signal and
    reduces coverage (no penalty).
  - Files likely involved: backend/app/scoring/ (signals), backend/tests/
  - Depends on: IC1-T2
  - Acceptance criteria covered: feature PRD §5 (Scoring contribution); PRD § Core
    Verification Philosophy (Entity Legitimacy), § Risk Signals; STRATEGY § Track:
    Risk scoring & explainability
  - Status: Complete (2026-09-24)

- IC1-T4 — LinkedIn adapter scaffold + stub provider (Tier 3)
  - Objective: Implement `verify_linkedin` adapter (`name = "linkedin"`,
    `tier = 3`); resolve via `linkedin_url` when present else name+domain (stub);
    emit `linkedin_company_url` / `_company_name` / `_employee_count` /
    `_founded_year` / `_website` / `_requester_match` evidence with attribution;
    register in `default_stages()` Tier-3 group; unresolved → `unavailable`.
  - Files likely involved: backend/app/adapters/linkedin.py (new),
    backend/app/pipeline/orchestrator.py, backend/tests/
  - Depends on: — (base platform exists)
  - Acceptance criteria covered: feature PRD §6 (Behavior, Evidence emitted);
    ARCHITECTURE § 4, § 2 stage 6; PRD § Verification Sources § Tier 3
  - Status: Complete (2026-09-24)

- IC1-T5 — LinkedIn consistency + scoring signals
  - Objective: Add website-match comparison (LinkedIn site vs. submitted `domain`),
    presence indicator, and best-effort requester↔company association; add
    `linkedin_established_presence` / `linkedin_requester_associated` (trust) and
    `linkedin_absent_or_thin` / `linkedin_website_mismatch` /
    `linkedin_recently_created` (elevated) to Representation (primary) + Entity
    (secondary). Absence weighted weakly (Tier 3).
  - Files likely involved: backend/app/pipeline/consistency.py,
    backend/app/scoring/, backend/tests/
  - Depends on: IC1-T4
  - Acceptance criteria covered: feature PRD §6 (Field comparison, Scoring,
    Calibration note); PRD § Risk Signals (recently created social presence);
    STRATEGY § Track: Risk scoring & explainability
  - Status: Complete (2026-09-24)

- IC1-T6 — Operator UI: Identity Corroboration panel + diff rows
  - Objective: Surface Tax-ID verification (status, registered name, match badge,
    source) and LinkedIn (page link, footprint, founded year, website-match badge,
    requester indicator, attribution) in the detail view; upgrade the Tax ID diff
    row; new risk signals render automatically via `contributing_signals`. Handle
    pending / not-available / populated states.
  - Files likely involved: frontend/src/components/DetailPanels.tsx,
    frontend/src/components/RegistrationDiff.tsx,
    frontend/src/api/client.ts (additive types only), frontend tests
  - Depends on: IC1-T2, IC1-T5
  - Acceptance criteria covered: feature PRD §9 (Frontend impact); PRD § Company
    Detail View (Registration Data, Contact Information, Risk Assessment);
    USERS § 1 (operator interface)
  - Status: Complete (2026-09-24)

- IC1-T7 — Report/export integration + state coverage tests
  - Objective: Confirm new evidence / comparisons / signals appear in
    `GET /reports/{id}` and `/reports/{id}/export` additively (no breaking change to
    existing consumers); add tests across pending / unavailable / populated for both
    features.
  - Files likely involved: backend/app/api (report/export — verify additive),
    backend/tests/, frontend tests
  - Depends on: IC1-T3, IC1-T5
  - Acceptance criteria covered: feature PRD §10 (API impact), §12 (Acceptance —
    Cross-cutting); PRD § API § Report Endpoint; USERS § 2 (API consumer)
  - Status: Complete (2026-09-24)

### Phase 2 — Wire Live FEIN Provider (US)
**Goal**
- Replace the FEIN stub with the selected real provider for US submissions.

**Exit Criteria**
- A real US FEIN resolves to live name/TIN match + entity status, driving the diff
  row and entity-layer signals; provider failures degrade gracefully.

**Tickets**
- IC2-T1 — Integrate selected FEIN provider (US-only)
  - Objective: Swap the stub for the IC0-T1 provider; real name/TIN match + entity
    status; secrets via env/config; wrap with existing caching + rate-limiting.
  - Files likely involved: backend/app/adapters/tax_id.py,
    backend/app/adapters/cache.py, backend/app/adapters/ratelimit.py,
    docs/RUNBOOK.md (env vars), backend/tests/
  - Depends on: IC1-T1, IC1-T2, IC1-T3; **Open Decision #5 (IC0-T1)**
  - Acceptance criteria covered: feature PRD §5, §12 (Acceptance — Feature A);
    ARCHITECTURE § 4 (caching, rate-limit, degradation)
  - Status: Blocked (Open Decision #5 / IC0-T1)

- IC2-T2 — FEIN signal calibration + non-US handling + tests
  - Objective: Tune entity-layer weighting conservatively (edge cases: newly issued
    FEINs, DBAs/name variants); confirm non-US → `unavailable` with no penalty;
    provider-sandbox/fixture tests for each typed failure mode.
  - Files likely involved: backend/app/scoring/, backend/tests/
  - Depends on: IC2-T1
  - Acceptance criteria covered: feature PRD §5, §13 (Risk 4 false positives);
    STRATEGY § Key metrics (triage precision/recall)
  - Status: Blocked (depends on IC2-T1)

### Phase 3 — Wire Live LinkedIn Provider
**Goal**
- Replace the LinkedIn stub with the selected compliant provider.

**Exit Criteria**
- A real company resolves to live presence/footprint/website data driving the
  corroboration panel and representation-layer signals; provider failures degrade
  gracefully; no Tier-1 verdict is overridden.

**Tickets**
- IC3-T1 — Integrate selected LinkedIn provider
  - Objective: Swap the stub for the IC0-T2 provider (official API or licensed
    data); secrets via env/config; wrap with caching + rate-limiting; enforce
    Tier-3 (never overrides Tier-1).
  - Files likely involved: backend/app/adapters/linkedin.py,
    backend/app/adapters/cache.py, backend/app/adapters/ratelimit.py,
    docs/RUNBOOK.md (env vars), backend/tests/
  - Depends on: IC1-T4, IC1-T5; **Open Decision #5 (IC0-T2)**
  - Acceptance criteria covered: feature PRD §6, §12 (Acceptance — Feature B);
    ARCHITECTURE § 4; PRD § Important Architectural Principle (scraping = fallback)
  - Status: Blocked (Open Decision #5 / IC0-T2)

- IC3-T2 — LinkedIn calibration + requester-association depth + tests
  - Objective: Confirm absence is weighted weakly; finalize best-effort
    requester-association behavior; tests including a Tier-1-vs-LinkedIn
    disagreement case proving LinkedIn does not override.
  - Files likely involved: backend/app/scoring/, backend/tests/
  - Depends on: IC3-T1
  - Acceptance criteria covered: feature PRD §6 (Calibration note), §12 (Acceptance
    — "never overrides Tier-1"); STRATEGY § Key metrics
  - Status: Blocked (depends on IC3-T1)

### Phase 4 — Hardening & Polish
**Goal**
- Meet the platform's robustness, audit, privacy, and quality bars for the new
  sources.

**Exit Criteria**
- Both adapters cached/rate-limited with full typed-failure coverage; sources-used
  + score-change history verified; requester-association PII handled per policy;
  docs and tests complete.

**Tickets**
- IC4-T1 — Adapter robustness coverage (caching, rate-limit, typed failures)
  - Objective: Ensure both new adapters use `CachedAdapter` / `RateLimitedAdapter`
    and the `SourceAvailabilityTracker`; cover timeout / unavailable / not-found /
    rate-limited.
  - Files likely involved: backend/app/adapters/cache.py, ratelimit.py,
    backend/tests/
  - Depends on: IC2-T1, IC3-T1
  - Acceptance criteria covered: feature PRD §13 (Risk 5 cost/latency);
    ARCHITECTURE § 4
  - Status: Todo
- IC4-T2 — Audit & score-change verification for new signals
  - Objective: Confirm new sources appear in sources-used audit and that new
    signals participate in re-analysis score-change history.
  - Files likely involved: backend/app/audit, backend/tests/
  - Depends on: IC1-T7
  - Acceptance criteria covered: feature PRD §11 (Auditability); PRD § Auditability
    Requirements; ARCHITECTURE § 6
  - Status: Todo
- IC4-T3 — PII review for requester-association data
  - Objective: Gate LinkedIn requester-association data behind role-based access +
    audit; align with retention policy.
  - Files likely involved: backend/app/auth, backend/app/models, docs
  - Depends on: IC1-T5; **Open Decision #7 (PII retention)**
  - Acceptance criteria covered: feature PRD §13 (Risk 3 PII); ARCHITECTURE § 6
    (PII handling)
  - Status: Blocked (Open Decision #7)
- IC4-T4 — Docs + test-coverage bar
  - Objective: Document provider env vars in RUNBOOK; update ARCHITECTURE § 4 source
    tiers to list the new sources; ensure BE + FE test coverage per project bar.
  - Files likely involved: docs/RUNBOOK.md, docs/ARCHITECTURE.md, tests
  - Depends on: IC2-T2, IC3-T2
  - Acceptance criteria covered: PRD § Technical Success (docs, test coverage);
    CLAUDE.md § Validation
  - Status: Todo

---

## Dependency Order
1. IC0-T1   (decision — parallelizable)
2. IC0-T2   (decision — parallelizable)
3. IC1-T1
4. IC1-T2
5. IC1-T3
6. IC1-T4
7. IC1-T5
8. IC1-T6
9. IC1-T7   ← stubbed vertical slice complete (no vendor dependency)
10. IC2-T1  (Open Decision #5 / IC0-T1)
11. IC2-T2
12. IC3-T1  (Open Decision #5 / IC0-T2)
13. IC3-T2
14. IC4-T1
15. IC4-T2
16. IC4-T3  (Open Decision #7)
17. IC4-T4

## Recommended Next Step
- Prerequisite: main BUILD_PLAN P4-T1 (e2e pipeline test) — **done 2026-09-24**. New
  adapters should be added to `tests/pipeline/test_pipeline_e2e.py` and `app/demo_data.py`
  stage lists (the guard test enforces this).
- Start with: **IC1-T1 — Tax-ID adapter scaffold + stub provider**, and open
  **IC0-T1 / IC0-T2** (provider decisions) to run in parallel.
- Why this is first: the entire Phase-1 slice is buildable behind deterministic
  stubs with **zero vendor dependency**, so it proves the end-to-end integration
  (evidence → diff → scoring → UI → export) before any licensing/ToS decision is
  cleared. The provider decisions (Open Decision #5) block only the Phase-2/3 live
  wiring, so pursuing them in parallel keeps the critical path short. IC1-T1 is the
  smallest first step: one new adapter against an already-existing contract.

## Deferred / Out of Scope
- **Non-US tax-ID jurisdictions** — feature PRD §15 (v1 US-only).
- **Individual identity proofing / document KYC** — feature PRD §3 Non-Goals.
- **Direct LinkedIn scraping** — feature PRD §6, §15; PRD § Known Challenges; gated
  by Open Decision #5 / ToS.
- **New operator actions or API resources** — feature PRD §3, §10 (additive only).
- **Provider selection (#5) and PII retention (#7)** — parked as Open Decisions
  until IC0-T1/IC0-T2 and IC4-T3 respectively.

## Update Rules
After each implementation pass:
- Update ticket status only as Todo / In Progress / Complete / Blocked
- Update Current Status (phase, ticket, blockers — including any Open Decision a
  ticket is blocked on)
- Set the next recommended ticket
- Do NOT add new scope unless /docs/PRD-identity-corroboration.md or a parent
  REQUIRED document changes
