---
anchor: PRD-IDV
title: Individual Identity Screening
status: draft
date: 2026-09-24
---

# PRD — Individual Identity Screening (EntityIQ, second offering)

**Author:** Jay Wolberg · **Date:** 2026-09-24 · **Status:** Approved for build. All conflicts in §[17] were resolved
2026-09-24 (C1–C10). PEP/adverse media (C7 phase 3) and the model (C3 phase 4)
are deferred.

**What this is:** a second, parallel offering in the EntityIQ repo. The existing offering
verifies **businesses** at registration (KYB, see [`PRD.md`](./PRD.md)). This one screens
**individuals**: it resolves whether a person in front of us is the same person as a
watchlist, PEP or adverse-media record, and records a defensible, replayable disposition.
It reuses the platform, stack, database and operator workbench (§[14]). Its subject, sources,
matching mechanics and decision record are its own.

---

## [1] The problem

**Before any compliance decision about a person can be made, one question has to be answered
correctly: *is the person in front of me the same person as this record?*** Every downstream
action (a sanctions block, a PEP enhanced-due-diligence flag, an adverse-media escalation, an
ongoing-monitoring alert) depends on that answer. Get it wrong and the error doesn't stay
local; it propagates into a filed report, a blocked customer, or a missed criminal.

This is **entity resolution for people**, and in compliance it's unusually hard because:

- **There is no global personal identifier.** National IDs, passports and tax numbers are
  per-jurisdiction, often absent from watchlists, and rarely present on both sides of a
  comparison.
- **Names are ambiguous and adversarial.** Transliteration (Cyrillic, Arabic, CJK to Latin),
  name-order inversion, patronymics and particles, diacritics, nicknames, initials and
  married/maiden names all produce legitimate variation. Someone evading screening
  exploits exactly that variation. The input distribution is actively hostile, not just noisy.
- **Common names collide at scale.** "John Smith" or "Mohammed Ali" matches thousands of
  unrelated people. Name similarity alone can't separate them; corroborating attributes
  (DOB, nationality, place of birth, ID numbers) must.
- **Evidence is partial and contradictory.** Watchlists often carry only a year of birth or
  several possible DOBs; sources disagree on nationality and spelling, and none is
  necessarily wrong.
- **The cost function is asymmetric.** A false negative is a regulatory failure. A false
  positive is an analyst's afternoon and a legitimate customer's delay. **The two can't be
  traded against each other.**

## [2] Why the naive approaches fail

| Approach | Failure |
|---|---|
| **Exact string match** | Misses every transliteration, name-order, diacritic and initialization variant. Recall collapses |
| **Fuzzy string distance** (Levenshtein, Jaro-Winkler) | No semantics. "Mohammed Ali" vs "Muhammad Ali" scores worse than many unrelated pairs. Tuned to catch the first, it floods the queue on common names |
| **Ask an LLM "are these the same person?"** | Produces a fluent, confident, unauditable answer. It can't be shown to an examiner or replayed, and its errors correlate with exactly the hard cases |
| **Pure ML classifier** | Can work, but becomes a "model" under bank model-risk-management expectations (validation, documentation, monitoring) and still doesn't explain a single decision |

**The gap:** the judgment is genuinely semantic, but the decision must be deterministic,
explainable and replayable. Those requirements pull in opposite directions, and resolving
that tension is the actual design problem.

## [3] Users and jobs

| User | Job to be done | What they need | Role in this repo |
|---|---|---|---|
| **Compliance analyst** | Dispose of a screening queue accurately and fast | Fewer alerts that were never real; for the ones that remain, the evidence assembled so the call takes minutes, not hours | `operator` |
| **Compliance officer / MLRO** | Defend the program to a regulator; set thresholds | Every decision reproducible, with inputs frozen and reasoning inspectable; threshold changes versioned | `lead` |
| **Regulator / examiner** | Verify the institution's controls work | An audit trail showing *what was known at the time*, not what the source says today | new read-only `examiner` role (§[17] C6) |
| **Integrating system** | Screen customers at onboarding from its own flow | A synchronous-enough API with a stable disposition contract | API key (existing service credentials) |
| **Engineering team** | Ship changes without silently breaking screening | A regression suite that fails loudly when matching behavior shifts | n/a |

## [4] Core design principle

> **The model extracts. Code decides.**

A language model is good at turning unstructured text into structured claims: pulling a date
of birth out of an adverse-media article, normalizing an address, recognizing that a name in
Cyrillic and one in Latin script are the same string. It's the wrong component to render the
final match verdict, because that verdict must be defensible after the fact.

```
  sources (sanctions lists · PEP lists · adverse media · customer-supplied ID data)
                              │
                              ▼
                  ┌───────────────────────┐
                  │  EXTRACTION (model)   │   unstructured → typed claims
                  │  name variants · DOB  │   every claim carries its source
                  │  nationality · IDs    │
                  │  addresses · roles    │
                  └───────────┬───────────┘
                              ▼
                  ┌───────────────────────┐
                  │  BLOCKING (code)      │   cheap candidate generation;
                  │  list → N candidates  │   recall-oriented, precision-agnostic
                  └───────────┬───────────┘
                              ▼
                  ┌───────────────────────┐
                  │  SCORING (code)       │   named, weighted, inspectable terms
                  │  each term cites the  │   NO free-floating signals
                  │  evidence it came from│
                  └───────────┬───────────┘
                              ▼
              ┌───────────────┴───────────────┐
         CLEAR          REVIEW (human)        MATCH
              └───────────────┬───────────────┘
                              ▼
                  ┌───────────────────────┐
                  │  DECISION RECORD      │   frozen inputs · rule version ·
                  │  append-only          │   every term · threshold · verdict
                  └───────────────────────┘
```

**Semantic judgment is allowed as evidence, never as the verdict.** A calibrated judgment
model (§[9]) may contribute a term to the score. It may never *be* the score.

Structured sources (sanctions CSV/XML, PEP datasets) don't need extraction; they're parsed
deterministically. Extraction applies to unstructured sources (adverse media, free-text
remarks fields such as OFAC's DOB/POB remarks).

## [5] Functional requirements

### 5.1 Subject intake
- **F0.** Accept a screening subject: full name (required); optionally name in original
  script, aliases, date of birth (full, partial or year-only), nationality/citizenship,
  country of residence, place of birth, gender, ID documents (type, number, issuing
  country), address. Subjects arrive through the API (integration keys) or the operator UI.
  Details in §[16].
- **F0a.** A subject may be linked to a KYB entity (for example an officer or beneficial
  owner discovered by the business offering). This is optional and one-directional in v1
  (§[17] C9).

### 5.2 Extraction
- **F1.** Extract typed claims from unstructured sources: name variants (with script),
  date(s) of birth, place of birth, nationality, ID numbers, addresses, roles/positions (for
  PEPs), and listing reason/program.
- **F2.** Every claim carries **source, retrieval timestamp and locator** (URL, document
  id, list entry id). A claim without provenance is discarded, not downgraded.
- **F3.** Extraction never sees the decision rule or the thresholds, so it can't be steered
  toward a verdict.

### 5.3 Blocking / candidate generation
- **F4.** Reduce the full watchlist/PEP space (tens of thousands to ~1M+ records depending
  on sources) to a bounded candidate set per subject.
- **F5.** Blocking is tuned for **recall only.** A candidate wrongly excluded here can never
  be recovered downstream, so this is the single highest-risk stage in the system.
- **F6.** Blocking keys must survive transliteration (ICU/ISO transliteration to Latin plus
  phonetic keys), name-order inversion, particles and patronymics, diacritics, nicknames and
  hypocoristics, initials, and single-token names.

### 5.4 Scoring
- **F7.** Score from named terms only. Each contributes a **declared weight** and
  **references ≥1 evidence claim.** No orphan signals.
- **F8.** Corroboration is worth more than similarity. A matching ID number or full DOB
  outranks a matching surname. **A hard conflict** (different full DOB, different ID of the
  same type and issuer) is a strong negative term, but it can't clear a hit on its own (lists
  carry errors), so it only lowers the band.
- **F9.** **A missing attribute or unreachable source reduces confidence; it never defaults
  to "no risk."** A subject screened with name only can't reach CLEAR against a name-only
  candidate above the review threshold.
- **F10.** Conflicting evidence is surfaced, not silently averaged.

### 5.5 Disposition
- **F11.** Three outcomes: **CLEAR / REVIEW / MATCH**. REVIEW is the explicit abstention
  band.
- **F12.** The system is **advisory**. It never auto-clears a potential sanctions hit. A
  human dispositions everything in REVIEW and MATCH. Whether a system CLEAR needs a human
  sign-off is decision §[17] C2.
- **F13.** Thresholds are configurable per list type (sanctions / PEP / adverse media), per
  jurisdiction and per customer risk tier. Changing one creates a new **rule version** and
  doesn't require re-running extraction.

### 5.6 Audit and decision record
- **F14.** Append-only, with no update path and no delete path, enforced in code *and* at
  the database layer (§[17] C4).
- **F15.** Each decision record stores the **entire frozen input** (subject snapshot,
  candidate records as retrieved, extracted claims, model outputs), the list versions, the
  rule version, every scoring term, the thresholds applied, the system disposition and the
  human disposition.
- **F16.** **Replay must reproduce the original verdict from the frozen inputs**, even
  after the underlying sources have changed. This is the requirement most systems fail: lists
  update, articles are edited, and "what did we know on March 3rd?" becomes unanswerable.

### 5.7 Ongoing monitoring (phase 2)
- **F17.** When a list version changes, previously screened subjects are re-screened against
  the delta. A new or changed candidate creates a new screening run; it never edits a
  past decision record.

## [6] Non-functional requirements

- **N1. Determinism.** Same frozen inputs → same verdict, forever. Any non-deterministic
  component must have its output **persisted as evidence at decision time**, never re-queried
  at replay.
- **N2. Explainability.** A compliance officer with no engineering background must be able
  to read why a decision was made, in one screen.
- **N3. Latency.** Onboarding screening: p95 under 10 s for structured sources
  (sanctions + PEP); adverse media may complete asynchronously within minutes, with a
  partial result shown in the meantime. This is a much tighter budget than KYB's under 2 hours,
  so it needs a per-offering run budget (§[17] C8). Real-time transaction screening is out
  of scope.
- **N4. Graceful degradation.** An unreachable source produces a *lower-confidence* result
  and a visible gap, never a silent pass.
- **N5. Privacy.** Subject data (DOB, ID numbers, nationality) is special-category-adjacent
  personal data. Access is role-gated and audited; retention follows §[17] C5; subject data
  is sent to a third-party model or API only under §[17] C3.

## [7] Metrics

**The headline metric is false-positive rate at fixed recall.**

Recall is fixed because a missed true hit is a regulatory failure, not a metric regression.
Within that constraint, everything that matters is precision, because false positives *are*
the cost: analyst hours and customer friction. Precision must never be bought with recall.

| Metric | Why |
|---|---|
| **FP rate @ 100% recall on a labeled set** | The only number that means anything commercially |
| **Blocking recall** on the adversarial corpus | A release gate; misses here are invisible downstream |
| **Alerts per 1,000 screenings** | Analyst hours, denominated in the buyer's own unit |
| **Abstention rate** (share landing in REVIEW) | Too low = overconfident; too high = no automation value |
| **Decision reproducibility** | % of replayed decisions returning the original verdict. **Target 100%** |
| **Time-to-disposition** | Does the assembled evidence actually make the human faster? |
| **Coverage** | Share of decisions where every intended source was reachable |

**Explicitly rejected: F1 score and accuracy.** Both average across an asymmetric cost
function and will reward a system that trades away recall.

**Evaluation data:** a versioned, synthetic **adversarial corpus** of name/attribute
collisions (transliterations, inversions, patronymics, nicknames, common-name clusters,
partial DOBs) checked into the repo, with fictional subjects only, like the KYB demo data. It
is regenerated on a cadence (§[11] Q4), and any change to matching must keep blocking
recall at 100% on it.

## [8] Non-goals

- **Auto-approval or auto-rejection of a customer.** The system orders and evidences work;
  a human decides.
- **Replacing the analyst.** It removes the alerts that were never real.
- **Document or biometric verification** (ID-document authenticity, liveness, selfie
  match). This offering answers "is this person on a list," not "is this person who they
  claim to be." Those are different products; possibly a later integration.
- **Transaction monitoring.** A different latency regime and architecture.
- **A universal person graph.** Resolution is the tractable core; a graph is what
  accumulates from it, not a prerequisite.
- **Beating any named vendor.** Not measurable from outside, and not the point.

## [9] Where a calibrated judgment model fits

String distance can't tell you that *Müller* and *Mueller*, or a Cyrillic and a Latin
rendering, or a patronymic-inverted name, plausibly denote one person. That's semantic.

A **"System One"-style model** (typed answers with calibrated probabilities rather than
generated text; for example TypeSafe's Jev) fits here because it returns something code can
consume: *"probability these two name records denote the same person, given this context."*
An entity-alignment pattern of one graded score across *different / plausibly-same / same*,
plus field-level yes/no companions combined by code, maps directly onto this problem, and
its three-way outcome matches §5.5.

**Constraints on using it:**
- It emits **an evidence claim** with a source, a probability and a model version. It
  doesn't emit the verdict.
- Its calibration holds **across populations, not for individual answers**, so it may narrow
  and rank but never finally decide a sanctions match.
- **Its output is persisted at decision time** to satisfy N1, because the model's own
  reproducibility can't be assumed.
- It's a "model" for model-risk purposes. Budget for validation and documentation.
- It's **optional in v1**: the deterministic path must meet the recall gate without it, and
  the model may only reduce REVIEW volume (§[17] C3).

## [10] Risks

| Risk | Mitigation |
|---|---|
| **Blocking silently drops a true match** | The highest-severity failure in the system. Adversarial corpus targeting blocking specifically; blocking recall is a CI release gate |
| **Threshold tuned on unrepresentative data** | Thresholds are versioned configuration, not code; re-evaluate per jurisdiction on labeled local data |
| **Audit trail that references live sources** | Freeze inputs at decision time. A record that says "see list" is not a record |
| **Analysts rubber-stamp the REVIEW queue** | If abstention is too broad, humans stop reading. Monitor the disposition-time distribution as a sign of automation bias |
| **Adversarial adaptation** | Evaders learn the thresholds. The collision corpus must be regenerated, not frozen once |
| **Over-trusting a calibrated model** | §[9] constraints; never in the decision path |
| **Subject PII sent to third parties** | Model/API calls follow §[17] C3; minimize fields; log what was sent, not the values |
| **Watchlist licensing** | Official government lists (OFAC, UN, EU, UK) are free to use; aggregated PEP/adverse-media data usually is not (§[17] C7) |

## [11] Open questions

1. What labeled data exists to measure FP-at-fixed-recall honestly? Without it, every
   number above is an assertion. (Starting point: the synthetic adversarial corpus, §[7].)
2. Where is the abstention band set, and does it move by jurisdiction and customer risk
   tier?
3. How is blocking recall validated, given a miss there is invisible downstream by
   construction?
4. What is the regeneration cadence for the adversarial corpus?
5. Should screening of a KYB entity's officers and beneficial owners reuse this offering
   directly, and does beneficial-ownership resolution need its own model? (See C9.)

## [12] Sources

| Tier | Source | Type | Notes |
|---|---|---|---|
| 1 | OFAC SDN + Consolidated (US) | Sanctions | Already ingested by the KYB `sanctions_screening` adapter (names only). Needs DOB/POB/nationality parsing from remarks/alt files |
| 1 | UN Security Council Consolidated List | Sanctions | Official XML, free |
| 1 | EU Financial Sanctions Files | Sanctions | Official XML, free |
| 1 | UK HMT/OFSI Consolidated List | Sanctions | Official CSV/XML, free |
| 2 | PEP dataset (vendor or OpenSanctions) | PEP | Licensing decision C7 |
| 3 | Adverse media | News/web | Unstructured, so extraction (§[4]) applies. Provider decision C7; no scraping-first |

Every list is ingested as a **versioned snapshot** (hash + retrieval time), and a decision
records the snapshot ids it used (F15/F16).

## [13] Scoring terms (initial catalog)

All terms reference the evidence claims they came from (F7). Weights live in the versioned
rule config (F13), not in code.

| Term | Direction | Evidence |
|---|---|---|
| `name_exact_normalized` | + | subject name vs candidate name/alias, post-normalization |
| `name_translit_equivalent` | + | transliteration/phonetic key equality |
| `name_token_reordered` | + | same tokens, different order |
| `name_semantic_same` (optional) | + | calibrated model claim (§[9]) |
| `dob_full_match` / `dob_year_match` / `dob_within_range` | + | DOB claims |
| `dob_conflict` | − | both full DOBs present and different |
| `dob_partial_conflict` | − (weaker) | DOBs disagree but at least one side is year- or month-only, or a range; still floors a name match at REVIEW |
| `id_number_match` | ++ | same ID type, issuer and number |
| `id_number_conflict` | − | same ID type and issuer, different number |
| `nationality_match` / `nationality_conflict` | +/− | nationality claims |
| `pob_match` | + | place of birth |
| `common_name_penalty` | − (applied to name terms only) | name frequency in the corpus |
| `source_unavailable` | confidence↓ | adapter outage (N4) |

## [14] How it fits this repo

**Platform stays the same:** Python ≥3.11 / FastAPI / SQLAlchemy + Alembic / Postgres (prod)
+ SQLite (dev, tests, demo) / Celery / React + TypeScript + Vite / npm / pytest + vitest.
No new language or database.

**Reused as is:**
- Operator auth, roles and session handling (`app/auth`), and integration API keys.
- The append-only audit recorder (`app/audit`), extended with screening event types.
- The source-adapter contract, typed failures, `RetryingAdapter`, cache and rate-limit
  wrappers (`app/adapters`).
- The orchestrator with per-stage timeouts, run budget, isolated stage sessions and
  partial-report behavior (`app/pipeline/orchestrator.py`, ticket 0001).
- Report/detail UI patterns, live refresh of in-flight runs, and the Activity panel.

**New, and separate from KYB:**
- Backend package `app/screening/` (models, stages, scoring, API router) so neither offering
  imports the other's domain code.
- API namespace `/screenings` (§[16]).
- Tables, prefixed or namespaced (§[15]).
- Frontend: a second nav section ("Individuals") with its own queue and detail pages.
- Its own `docs/BUILD_PLAN-individual-screening.md` when planning starts.

**Shared list infrastructure:** the OFAC loader moves to a shared `app/lists/` module that
both offerings consume. KYB keeps screening company names; this offering adds person
records with attributes.

## [15] Data model (sketch)

| Table | Purpose |
|---|---|
| `screening_subject` | The person as submitted; PII columns nullable for retention/anonymization |
| `screening_run` | One screening of a subject against a set of list snapshots; lifecycle + timing (same shape as `verification_run`) |
| `list_snapshot` | Versioned list ingest: source, retrieved_at, content hash, record count |
| `watchlist_record` | Parsed person record from a snapshot (names, DOBs, nationalities, IDs, program) |
| `claim` | Typed extracted claim with provenance (F2) |
| `candidate` | Blocking output: subject × record, with the blocking key that produced it |
| `scoring_term` | Named term, weight, direction, evidence claim ids |
| `rule_version` | Versioned thresholds + weights (F13) |
| `decision_record` | Frozen input bundle + terms + thresholds + system disposition; append-only (F14–F16) |
| `disposition` | Human disposition referencing a decision record; append-only |

## [16] API and UI

**API (integration keys or operator session):**
- `POST /screenings`: submit a subject. Returns `run_id`; structured sources usually
  complete within N3, so the response may include the disposition when it's ready in time.
- `GET /screenings/{run_id}`: subject, candidates, terms, system disposition, human
  disposition, and `run` timing/progress (same shape as KYB reports).
- `POST /screenings/{run_id}/disposition`: operator/lead records CLEAR / MATCH with
  notes (a new append-only row).
- `POST /screenings/{run_id}/replay`: lead/examiner; recomputes from the frozen inputs and
  returns whether it matches the original verdict (F16).
- `GET /screenings`: queue with filters by system disposition, list type and age.

**UI:** a screening queue sorted by disposition severity; a detail page with a side-by-side
subject vs candidate comparison, each term with its evidence, list snapshot and rule
version, source coverage gaps, and a disposition form. The replay result is shown to
lead/examiner.

## [17] Conflicts and decisions needed

Each item names the conflict, the options, and a recommendation. **Resolved** items record
the owner's decision; the rest are open until confirmed.

- **C1: Disposition vocabulary.** **Resolved 2026-09-24: as recommended.** This PRD uses CLEAR / REVIEW / MATCH; KYB uses
  `pre_clear` / `review` / `escalate`. *Recommendation:* keep separate vocabularies per
  offering (they mean different things: "not this person" vs "business looks legitimate"),
  and share only the UI badge component.
- **C2: Does CLEAR need a human?** **Resolved 2026-09-24: guarded auto-CLEAR, as recommended.** KYB never auto-decides (`pre_clear` still waits for an
  operator). F12 only requires humans for REVIEW/MATCH. *Recommendation:* CLEAR is recorded
  as a **system disposition** that closes without human action, allowed only when every
  candidate scored below the clear threshold **and** no source was unavailable. It's sampled
  for QA by leads. This departs from KYB's rule and needs explicit sign-off.
- **C3: Models and third-party data flow.** **Resolved 2026-09-24: deterministic v1, as recommended.** The existing product is "no ML, no black-box",
  with no LLM dependency; this PRD adds model extraction and an optional calibrated model,
  both of which would send subject PII to a vendor. *Recommendation:* v1 ships
  **deterministic only** (structured lists, rule-based normalization/transliteration). Add
  the model path in phase 3 behind a provider ADR covering vendor, data processing terms,
  field minimization and model-risk documentation.
- **C4: Append-only at the DB layer.** **Resolved 2026-09-24: as recommended.** Today the audit log is append-only by convention
  (no update/delete helpers). F14 requires database enforcement. *Recommendation:* add
  triggers that reject UPDATE/DELETE on `decision_record`, `disposition` and `audit_event`
  (Postgres `BEFORE UPDATE OR DELETE` raising; SQLite `RAISE(ABORT)`), in one migration that
  covers both offerings. This conflicts with ADR-0002's anonymization of *submissions* only
  if it touches those tables. It doesn't: PII lives in `screening_subject`, and decision
  records reference it by id.
- **C5: PII retention for screening subjects.** **Resolved 2026-09-24: crypto-shred after the 5-year AML period, as recommended. To be written up as an ADR in phase 1.** ADR-0002 covers KYB submissions. Screening
  records sit under AML record-keeping (typically 5 years after the relationship ends),
  which conflicts with minimization, and frozen inputs (F15) contain PII by design.
  *Recommendation:* a follow-on ADR in which decision records keep frozen inputs for the
  regulatory period and are then anonymized by **crypto-shredding** (per-subject encryption
  key destroyed), which preserves append-only while making PII unrecoverable.
- **C6: Examiner role.** **Resolved 2026-09-24: as recommended.** There's no read-only role today. *Recommendation:* add
  `examiner` (read-only, all screening and audit views, replay; no dispositions), with the
  same auth path and tests.
- **C7: PEP and adverse-media sources.** **Resolved 2026-09-24: official sanctions lists only in v1, as recommended.** These are licensed data (same class of problem as
  KYB Open Decision #5). *Recommendation:* v1 = official sanctions lists only (free);
  decide PEP and adverse-media providers in a separate ADR before phase 2.
- **C8: Run budget per offering.** **Resolved 2026-09-24: as recommended.** Ticket 0001's run/stage budgets are process-wide env vars
  sized for KYB's under-2h target; screening needs seconds. *Recommendation:* make budgets
  per-pipeline (Orchestrator args per offering, env `ENTITYIQ_SCREENING_*`), and route
  screening to its own Celery queue so long KYB runs can't starve it.
- **C9: Coupling with KYB.** **Resolved 2026-09-24: as recommended.** Officers and owners found by KYB are natural screening
  subjects. *Recommendation:* v1 allows an optional `kyb_entity_id` link and a manual
  "screen this officer" action; automatic cross-offering screening and feeding screening
  results into KYB risk scores come later and need their own decision.
- **C10: Naming.** **Resolved 2026-09-24: as recommended.** The repo, product and docs are named "EntityIQ" and are business-centric.
  *Recommendation:* keep the repo name; call the offerings "EntityIQ Business
  Verification" and "EntityIQ Individual Screening" in the UI and docs.

## [18] Delivery phases (proposed)

1. **Foundations:** shared `app/lists/` with versioned snapshots (OFAC, UN, EU, UK person
   records with attributes); screening data model + migrations; DB-level append-only (C4);
   adversarial corpus v1 + blocking-recall gate in CI.
2. **Deterministic screening slice:** intake API, normalization/transliteration, blocking,
   scoring terms (§[13]), rule versions, decision records with frozen inputs, replay
   endpoint, operator queue and detail UI, examiner role.
3. **PEP + adverse media:** after the C7 decisions; extraction for unstructured sources.
4. **Calibrated model (optional):** after C3; evidence-only term with persisted outputs.
5. **Ongoing monitoring:** re-screen on list delta (F17).

---

## Appendix — what the existing KYB offering already provides

The business-verification offering in this repo (backend 469 tests, frontend 32) already
implements several of these principles; the screening offering should reuse them rather than
re-derive them.

| Requirement | Existing implementation |
|---|---|
| F7: no orphan signals | Scoring engine invariant: every contributing signal must reference ≥1 evidence row; every signal and weight is explicit |
| F9: missing source ≠ low risk | Missing or unavailable sources reduce confidence rather than defaulting to low risk |
| F11/F12: three-way, advisory | `pre_clear` / `review` / `escalate` triage; the engine makes no approve/reject decision |
| F14: append-only audit | The recorder exposes `record_event()` only, with no update/delete helper (code-level only; see C4) |
| N4: graceful degradation | Typed adapter failures, retries, per-stage timeouts, run budget, partial reports (ticket 0001) |
| Blocking + resolution | `pipeline/resolve.py`: name-token + domain scoring, ranked candidates, conflicting-identity signal when the top two are within 0.05 |
| Sanctions screening | OFAC SDN adapter: alias matching with legal-suffix stripping, injectable fetcher for deterministic tests |

**Known gap, and why §[9] and the adversarial corpus exist:** `resolve.py`'s matcher is
token overlap plus domain scoring, built for company names. It will fail on exactly the
collisions this PRD is about: transliteration, name-order inversion, patronymics and
common-name clusters. The screening offering needs its own person-name normalization and
blocking, validated against the adversarial corpus described in §[7].
