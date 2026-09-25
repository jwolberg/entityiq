# Build Plan — Individual Identity Screening (second offering)

> **Offering-scoped plan.** This plans only the offering in
> [identity-verification-PRD.md](./identity-verification-PRD.md) (anchor `PRD-IDV`).
> The business-verification plan lives in [BUILD_PLAN.md](./BUILD_PLAN.md) and is
> left untouched. Tickets are prefixed **`IS-`** so they don't collide with `P-` or `IC-`.

## Project
- Name: EntityIQ — Individual Screening
- Summary: Screen **individuals** against sanctions lists (later PEP and adverse
  media). Resolve whether a subject is the same person as a list record with
  deterministic, explainable scoring, and record an append-only decision with
  frozen inputs that can be replayed to the same verdict. It's a parallel offering
  on the existing EntityIQ platform: same stack, database, auth, audit and workbench.

## Source of Truth
- Offering requirements: /docs/identity-verification-PRD.md (PRD-IDV)
- Platform and conventions: /docs/ARCHITECTURE.md, /CLAUDE.md
- Existing offering (reused infrastructure): /docs/PRD.md, /docs/BUILD_PLAN.md
- PII baseline: /docs/decisions/0002-pii-retention-policy.md (ADR-0002, PR #4)
- Time budgets / isolated stages: backlog ticket 0001 (PR #3)

## Planning Assumptions
- **PRD-IDV is authoritative for this offering.** Platform rules (stack, append-only
  audit, no auto-approval of customers, no live network in tests) come from CLAUDE.md
  and still apply.
- **Resolved decisions (2026-09-24):** C2 guarded auto-CLEAR; C3 deterministic v1
  (no LLM/model, so no subject PII sent to vendors); C5 crypto-shred after the 5-year
  AML period; C7 official sanctions lists only in v1 (OFAC, UN, EU, UK).
- **All remaining conflicts were confirmed as recommended (2026-09-24, IS0-T1):**
  C1 separate disposition vocabulary; C4 DB-level append-only triggers; C6 `examiner`
  role; C8 per-offering budgets + a dedicated Celery queue; C9 optional manual KYB
  link; C10 offering names.
- **Deferred to icebox (2026-09-24):** Phase 3 (IS3-*, PEP + adverse media) and
  Phase 4 (IS4-*, model), pending licensing and model decisions.
- **New dependencies need approval** (global rule): per-subject encryption (C5) and
  transliteration/phonetic keys (F6). IS0-T2 decides them; nothing is added before.
- **Deterministic first:** Phases 1–2 ship with no model. The calibrated model
  (PRD §[9]) is Phase 4 and optional; it may only reduce REVIEW volume, never
  change the recall gate.
- **Test data is synthetic and fictional** (like `app/demo_data.py`). No real
  person's data is committed. Real list snapshots are fetched at runtime and never
  committed; tests use small recorded fixtures of public list entries.
- **Postgres coverage:** CI runs SQLite only today. DB-level checks (triggers,
  encryption) must also be verified on Postgres, via the docker-compose ticket
  (backlog 0025) or a Postgres CI service added in IS1-T4.

## Architecture Notes
- **Package boundary:** new domain code lives in `backend/app/screening/` (models,
  stages, scoring, API router). Shared list ingestion moves to `backend/app/lists/`.
  KYB code doesn't import `app/screening`, and screening doesn't import KYB domain
  modules (PRD §[14]).
- **Reuse:** `app/auth` (roles, API keys), `app/audit` (`record_event`), the adapter
  contract and wrappers (`RetryingAdapter`, cache, rate limit), and the orchestrator
  with stage/run budgets and isolated stage sessions (ticket 0001). Frontend reuses
  the report/detail patterns and live refresh.
- **Pipeline (per screening run):** normalize subject → load list snapshot(s) →
  block candidates → score terms → dispose (CLEAR/REVIEW/MATCH) → write decision
  record. Each is a `PipelineStage`; the budget is seconds (C8, PRD N3).
- **Data model:** PRD §[15] tables, Alembic migrations that run on SQLite and
  Postgres. Subject PII is encrypted per subject (C5); decision records reference
  subjects by id and hold frozen inputs encrypted with the same subject key.
- **Rule versions:** thresholds and weights are versioned config rows
  (`rule_version`), not code (F13). A decision stores the rule version id.
- **Replay:** recompute from `decision_record` frozen inputs + rule version only,
  with no list or network access (F16, N1).
- **API:** `/screenings` namespace (PRD §[16]). **UI:** an "Individuals" nav section
  with its own queue and detail pages.

## Current Status
- Overall status: v1 built (2026-09-24). Phases 0–2 and 5 complete (backlog
  0027–0045, 0051, 0052); Phases 3–4 deferred (icebox 0046–0050).
- Current phase: done for v1; next is tuning thresholds on labeled data (see
  implementation-notes: abstention 100% on the corpus with default match_at 0.9)
- Current ticket: none (Phase 3/4 await licensing and model decisions)
- Blockers: none for Phase 0. Phase 1 tickets that depend on C4/C6/C8 or on new
  libraries wait for IS0-T1 / IS0-T2. Phase 3 is blocked on PEP/adverse-media
  licensing (IS3-T1); Phase 4 on a model-provider decision (IS4-T1).
- Prerequisites in flight: PR #3 (ticket 0001: stage/run budgets, isolated stage
  sessions, RetryingAdapter) and PR #4 (ADR-0002). Phase 1 builds on both; merge
  them first.

---

## Phase Breakdown

### Phase 0 — Decisions
**Goal**
- Settle the open conflicts and the dependency choices that shape schema and APIs.

**Exit Criteria**
- C1, C4, C6, C8, C9 and C10 are confirmed or overridden in PRD-IDV §[17]; the new
  dependencies are chosen, recorded, and approved.

**Tickets**
- IS0-T1 — Confirm open conflicts C1, C4, C6, C8, C9, C10
  - Objective: Get an owner decision on each open item in PRD-IDV §[17] and record
    it in the PRD (Resolved + date).
  - Files likely involved: docs/identity-verification-PRD.md
  - Depends on: —
  - Acceptance criteria covered: PRD-IDV §[17]
  - Status: Complete (2026-09-24)
- IS0-T2 — Choose encryption and name-matching libraries (ADR)
  - Objective: Pick the libraries for per-subject envelope encryption (C5) and
    transliteration/phonetic keys (F6). Weigh maintenance, license, wheel
    availability and determinism. Record in an ADR and pin versions.
  - Files likely involved: docs/decisions/ (new ADR), backend/pyproject.toml
  - Depends on: —
  - Acceptance criteria covered: PRD-IDV F6, C5, N1 (determinism)
  - Status: Complete (2026-09-24)

### Phase 1 — Foundations
**Goal**
- Shared, versioned list ingestion; the screening schema; database-enforced
  append-only; per-subject encryption; the adversarial corpus; per-offering budgets.

**Exit Criteria**
- Official lists load as versioned snapshots with person attributes; screening
  tables migrate on SQLite and Postgres; UPDATE/DELETE on append-only tables fails
  at the database; the corpus and its harness are in the repo; KYB behavior is
  unchanged (full suite green).

**Tickets**
- IS1-T1 — Extract shared list ingestion into `app/lists/` with versioned snapshots
  - Objective: Move the OFAC loader out of the KYB sanctions adapter into
    `app/lists/`; add `list_snapshot` (source, retrieved_at, content hash, count).
    KYB consumes it with no behavior change.
  - Files likely involved: backend/app/lists/ (new), backend/app/adapters/sanctions.py,
    backend/app/models/, alembic migration, backend/tests/
  - Depends on: —
  - Acceptance criteria covered: PRD-IDV §[12], §[14] (shared list infrastructure), F15
  - Status: Complete (2026-09-24)
- IS1-T2 — Parse person records from OFAC, UN, EU and UK lists
  - Objective: Parse individuals into `watchlist_record`: names/aliases (with
    script), DOBs (full/partial/year/range), POB, nationalities, ID documents,
    program. OFAC DOB/POB come from the remarks/alt data.
  - Files likely involved: backend/app/lists/parsers/ (new), backend/tests/fixtures/
  - Depends on: IS1-T1
  - Acceptance criteria covered: PRD-IDV F1–F2 (structured sources), §[12]
  - Status: Complete (2026-09-24)
- IS1-T3 — Screening data model and migrations
  - Objective: Add the PRD §[15] tables (subject, run, candidate, claim,
    scoring_term, rule_version, decision_record, disposition) under
    `app/screening/models`.
  - Files likely involved: backend/app/screening/models/ (new), alembic migration
  - Depends on: IS0-T1 (C1 vocabulary)
  - Acceptance criteria covered: PRD-IDV §[15], F13, F15
  - Status: Complete (2026-09-24)
- IS1-T4 — Enforce append-only at the database layer
  - Objective: Add triggers rejecting UPDATE/DELETE on `audit_event`,
    `decision_record` and `disposition` (Postgres raise; SQLite `RAISE(ABORT)`).
    Verify on both engines (add a Postgres CI service or use the backlog 0025 compose).
  - Files likely involved: alembic migration, .github/workflows/ci.yml, backend/tests/
  - Depends on: IS1-T3, IS0-T1 (C4)
  - Acceptance criteria covered: PRD-IDV F14, C4
  - Status: Complete (2026-09-24)
- IS1-T5 — Per-subject envelope encryption and crypto-shred
  - Objective: Encrypt subject PII and frozen inputs with a per-subject data key,
    wrapped by a master key from env. Shredding destroys the data key after the
    retention period; a scheduled job applies it. Write ADR-0004 for the policy (C5); ADR-0003 records the libraries.
  - Files likely involved: backend/app/screening/crypto.py (new),
    backend/app/screening/retention.py (new), docs/decisions/ (ADR-0003), RUNBOOK
  - Depends on: IS1-T3, IS0-T2
  - Acceptance criteria covered: PRD-IDV N5, C5
  - Status: Complete (2026-09-24)
- IS1-T6 — Adversarial name corpus v1 and evaluation harness
  - Objective: Check in a synthetic, fictional corpus of collision cases
    (transliteration, inversion, patronymics, nicknames, initials, common-name
    clusters, partial DOBs) with expected labels, plus a harness reporting blocking
    recall and FP rate at fixed recall.
  - Files likely involved: backend/tests/screening/corpus/ (new), backend/app/screening/eval.py
  - Depends on: —
  - Acceptance criteria covered: PRD-IDV §[7] (evaluation data, metrics), §[10]
  - Status: Complete (2026-09-24)
- IS1-T7 — Per-offering run budgets and a dedicated screening Celery queue
  - Objective: Make stage/run budgets per pipeline (screening defaults in seconds via
    `ENTITYIQ_SCREENING_*`), and route screening tasks to their own queue so KYB runs
    can't starve them.
  - Files likely involved: backend/app/worker.py, backend/app/screening/tasks.py (new),
    docs/RUNBOOK.md
  - Depends on: IS0-T1 (C8)
  - Acceptance criteria covered: PRD-IDV N3, C8
  - Status: Complete (2026-09-24)

### Phase 2 — Deterministic screening slice
**Goal**
- End to end: submit a person → candidates → scored terms → disposition → frozen
  decision record → operator review → replay, all deterministic.

**Exit Criteria**
- Blocking recall is 100% on the corpus (CI gate); replay reproduces 100% of
  decisions; an operator can work the queue in the UI; the examiner can replay;
  demo data covers all three dispositions.

**Tickets**
- IS2-T1 — Screening intake API
  - Objective: `POST /screenings` (operator session or API key) validates the subject
    (F0), creates the run, enqueues it on the screening queue, and records audit
    events. It returns the disposition if ready within the budget.
  - Files likely involved: backend/app/screening/api.py (new), schemas, tests
  - Depends on: IS1-T3, IS1-T5, IS1-T7
  - Acceptance criteria covered: PRD-IDV F0, §[16], N3
  - Status: Complete (2026-09-24)
- IS2-T2 — Person-name normalization and blocking with a recall gate
  - Objective: Normalize names (script, diacritics, particles, order) and generate
    blocking keys (transliteration + phonetic + token-set). Produce candidates with
    the key that matched. Enable the CI gate: blocking recall = 100% on the corpus.
  - Files likely involved: backend/app/screening/normalize.py, blocking.py (new), CI
  - Depends on: IS1-T2, IS1-T6, IS0-T2
  - Acceptance criteria covered: PRD-IDV F4–F6, §[7] (recall gate), §[10]
  - Status: Complete (2026-09-24)
- IS2-T3 — Scoring terms and versioned rule config
  - Objective: Implement the PRD §[13] term catalog (every term cites ≥1 claim) with
    weights and thresholds in `rule_version` rows; include name-frequency data for
    the common-name penalty; surface conflicts rather than averaging them.
  - Files likely involved: backend/app/screening/scoring.py (new), rule seed, tests
  - Depends on: IS2-T2
  - Acceptance criteria covered: PRD-IDV F7–F10, F13, §[13]
  - Status: Complete (2026-09-24)
- IS2-T4 — Disposition engine with guarded auto-CLEAR and a frozen decision record
  - Objective: Map scores to CLEAR/REVIEW/MATCH per the rule version. Auto-CLEAR only
    when every candidate is below the clear threshold and no source was unavailable
    (C2). Write the decision record with the full frozen input bundle, list snapshot
    ids, rule version and terms.
  - Files likely involved: backend/app/screening/dispose.py (new), tests
  - Depends on: IS2-T3, IS1-T4
  - Acceptance criteria covered: PRD-IDV F11–F12, F15, C2, N4
  - Status: Complete (2026-09-24)
- IS2-T5 — Replay endpoint and reproducibility check
  - Objective: `POST /screenings/{id}/replay` recomputes from the frozen inputs only
    (no list or network access) and reports match/mismatch; CI replays every corpus
    decision.
  - Files likely involved: backend/app/screening/replay.py, api.py, tests
  - Depends on: IS2-T4
  - Acceptance criteria covered: PRD-IDV F16, N1, §[7] (reproducibility 100%)
  - Status: Complete (2026-09-24)
- IS2-T6 — Screening read and human-disposition API
  - Objective: `GET /screenings` (queue filters), `GET /screenings/{id}` (subject,
    candidates, terms with evidence, run timing), and
    `POST /screenings/{id}/disposition` (append-only row with notes); all audited.
  - Files likely involved: backend/app/screening/api.py, schemas, tests
  - Depends on: IS2-T4
  - Acceptance criteria covered: PRD-IDV F12, §[16], N2
  - Status: Complete (2026-09-24)
- IS2-T7 — Read-only examiner role
  - Objective: Add `examiner` (read-only screening and audit views, replay; no
    dispositions or writes) on the existing auth path, with permission tests.
  - Files likely involved: backend/app/auth/, backend/app/models/operator.py, migration, tests
  - Depends on: IS0-T1 (C6), IS2-T5, IS2-T6
  - Acceptance criteria covered: PRD-IDV §[3], C6
  - Status: Complete (2026-09-24)
- IS2-T8 — Individuals queue and detail UI
  - Objective: An "Individuals" nav section: a queue sorted by disposition severity;
    a detail page with subject vs candidate side by side, each term with its evidence,
    list snapshot and rule version, coverage gaps, a disposition form, and replay for
    lead/examiner; live refresh for in-flight runs.
  - Files likely involved: frontend/src/pages/screening/ (new), api/client.ts, tests
  - Depends on: IS2-T6, IS2-T7
  - Acceptance criteria covered: PRD-IDV §[16] (UI), N2
  - Status: Complete (2026-09-24)
- IS2-T9 — Screening demo data and end-to-end test
  - Objective: Fictional subjects spanning CLEAR / REVIEW / MATCH against recorded
    list fixtures, loaded by the demo script; an e2e test from submit to disposition
    to replay.
  - Files likely involved: backend/app/screening/demo_data.py (new), scripts/demo.sh, tests
  - Depends on: IS2-T5, IS2-T6
  - Acceptance criteria covered: PRD-IDV §[18] phase 2, §[7]
  - Status: Complete (2026-09-24)
- IS2-T10 — Screening metrics report
  - Objective: Report FP rate at 100% recall, abstention rate, coverage and
    reproducibility over the corpus as a CI artifact; a lead-only metrics view is
    optional.
  - Files likely involved: backend/app/screening/eval.py, CI workflow
  - Depends on: IS2-T5
  - Acceptance criteria covered: PRD-IDV §[7]
  - Status: Complete (2026-09-24)

### Phase 3 — PEP and adverse media
**Goal**
- Add PEP screening and adverse media once sources are licensed.

**Exit Criteria**
- Licensed PEP data screens with role/position terms; adverse media produces
  provenance-bearing claims; both degrade gracefully and pass the recall gate.

**Tickets**
- IS3-T1 — Decide PEP and adverse-media sources (ADR)
  - Objective: Choose providers (for example an OpenSanctions commercial license or a
    vendor); clear license, cost and data-processing terms. For adverse media,
    decide between a provider-structured feed and in-house extraction (which ties
    to C3 / IS4-T1).
  - Files likely involved: docs/decisions/ (new ADR), PRD-IDV §[12]
  - Depends on: —
  - Acceptance criteria covered: PRD-IDV C7, §[12], §[10] (licensing)
  - Status: Deferred (icebox 2026-09-24)
- IS3-T2 — PEP list adapter and PEP scoring terms
  - Objective: Ingest the chosen PEP dataset as versioned snapshots; add terms for
    position, jurisdiction and relationship (RCA); list-type thresholds.
  - Files likely involved: backend/app/lists/, backend/app/screening/scoring.py, tests
  - Depends on: IS3-T1, IS2-T4
  - Acceptance criteria covered: PRD-IDV §[12], F13
  - Status: Deferred (icebox 2026-09-24)
- IS3-T3 — Adverse-media adapter with provenance-bearing claims
  - Objective: Ingest adverse media per IS3-T1. Every claim carries source,
    timestamp and locator (F2); the async completion updates the run's partial result
    (N3).
  - Files likely involved: backend/app/screening/adverse_media.py (new), tests
  - Depends on: IS3-T1, IS2-T4
  - Acceptance criteria covered: PRD-IDV F1–F3, N3, N4
  - Status: Deferred (icebox 2026-09-24)

### Phase 4 — Calibrated judgment model (optional)
**Goal**
- Reduce REVIEW volume with a calibrated name-equivalence term, as evidence only.

**Exit Criteria**
- The model term is persisted at decision time, replay doesn't call the model, the
  recall gate is unchanged, and the REVIEW rate drops on the corpus.

**Tickets**
- IS4-T1 — Decide model provider and data flow (ADR)
  - Objective: Choose between a vendor (for example a System-One-style calibrated
    model) and self-hosted; set data processing terms, field minimization, and a
    model-risk documentation plan (C3).
  - Files likely involved: docs/decisions/ (new ADR)
  - Depends on: —
  - Acceptance criteria covered: PRD-IDV §[9], C3, N5
  - Status: Deferred (icebox 2026-09-24)
- IS4-T2 — Evidence-only model term with persisted outputs
  - Objective: Add `name_semantic_same` as a claim with probability and model version,
    persisted in the decision record; replay reads it and never re-queries.
  - Files likely involved: backend/app/screening/model_term.py (new), tests
  - Depends on: IS4-T1, IS2-T5
  - Acceptance criteria covered: PRD-IDV §[9], N1
  - Status: Deferred (icebox 2026-09-24)

### Phase 5 — Ongoing monitoring
**Goal**
- Re-screen existing subjects when lists change.

**Exit Criteria**
- A new list snapshot triggers delta re-screening that creates new runs (never edits
  old decisions); new hits appear in the queue.

**Tickets**
- IS5-T1 — List delta detection and re-screening
  - Objective: Diff snapshots (added/changed/removed person records), block active
    subjects against the delta only, and create new screening runs for affected
    subjects.
  - Files likely involved: backend/app/lists/delta.py, backend/app/screening/monitor.py (new)
  - Depends on: IS2-T4
  - Acceptance criteria covered: PRD-IDV F17
  - Status: Complete (2026-09-24)
- IS5-T2 — Monitoring alerts in the queue
  - Objective: Surface monitoring-originated runs in the Individuals queue with their
    trigger (snapshot + changed record) and link to the prior decision.
  - Files likely involved: frontend/src/pages/screening/, backend/app/screening/api.py
  - Depends on: IS5-T1, IS2-T8
  - Acceptance criteria covered: PRD-IDV F17, N2
  - Status: Complete (2026-09-24)

---

## Dependency Order
1. IS0-T1, IS0-T2   (decisions; parallel)
2. IS1-T1 → IS1-T2
3. IS1-T6           (parallel with 2)
4. IS1-T3 → IS1-T4, IS1-T5
5. IS1-T7
6. IS2-T2 → IS2-T3 → IS2-T4 → IS2-T5
7. IS2-T1, IS2-T6
8. IS2-T7 → IS2-T8
9. IS2-T9, IS2-T10  ← deterministic offering usable end to end
10. IS3-T1 → IS3-T2, IS3-T3
11. IS4-T1 → IS4-T2 (optional)
12. IS5-T1 → IS5-T2

## Recommended Next Step
- Start with: **IS0-T1** (confirm the six open conflicts) and **IS0-T2** (library
  choices), which are quick owner decisions. In parallel, **IS1-T1** (shared
  `app/lists/`) and **IS1-T6** (adversarial corpus) depend on neither.
- Why: the corpus is the release gate for everything after it, and shared list
  ingestion is the only foundation both offerings touch. Doing them first de-risks the
  schema and blocking work.
- Merge PR #3 and PR #4 before Phase 1 code starts.

## Deferred / Out of Scope
- **Document and biometric verification** (ID authenticity, liveness): PRD-IDV §[8].
- **Transaction monitoring / real-time screening:** PRD-IDV §[8], N3.
- **Automatic cross-offering screening** (KYB officers auto-screened, screening
  results feeding KYB risk): PRD-IDV C9; v1 is a manual link only.
- **Universal person graph:** PRD-IDV §[8].
- **Auto-rejection or auto-approval of customers:** PRD-IDV §[8], F12.

## Update Rules
After each implementation pass:
- Update ticket status only as Todo / In Progress / Complete / Blocked
- Update Current Status (phase, ticket, blockers — including any open decision a
  ticket is blocked on)
- Set the next recommended ticket
- Do NOT add new scope unless /docs/identity-verification-PRD.md changes
