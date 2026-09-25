---
anchor: PLAN-IS-GAPS
title: Individual screening — requirements vs PRD vs build, and gap-closure plan
status: draft
date: 2026-09-25
---

# Individual screening: requirements → PRD → build gap analysis

**Inputs compared**

- **Requirements:** the "Key functions / Eval characteristics" brief (five stages plus an
  optional judgment model, the eval metrics, and seven adversarial case types).
- **PRD:** [`identity-verification-PRD.md`](../identity-verification-PRD.md) (PRD-IDV).
- **Build:** `backend/app/screening/`, `backend/app/lists/`,
  `backend/tests/screening/` (corpus v2), `frontend/src/pages/screening/`, CI
  `screening-metrics` step, and [`BUILD_PLAN-individual-screening.md`](../BUILD_PLAN-individual-screening.md)
  (Phases 0–2 and 5 done; 3–4 iceboxed).

**How to read the status columns**

- ✅ covered · ◐ partial · ❌ missing · ⏸ intentionally deferred (a recorded decision)
- **Evidence:** *verified* = a command was run in this session; *inferred* = read
  the code, didn't run anything.

---

## [1] Summary

The PRD captures almost every requirement in the brief, often in the same words
(F1–F17, §[7], §[9]). The build follows the PRD closely for the deterministic
path: blocking, scoring, disposition, append-only decisions and replay. The gaps
fall into four groups:

1. **The brief is wider than the PRD in two places.**
   - It lists **corporate** attributes and adversarial cases: legal/trading names,
     registration numbers, officers, ownership chains, legal-suffix noise and
     shell-alias chains.
   - It wants eval cases **generated against real OFAC SDN and OpenSanctions
     data**. PRD-IDV covers people only, and the repo rule is fictional test data.
   - Both need an owner decision before any build work (§[4] D1, D2).
2. **The PRD requires it, the build hasn't done it.**
   - Per-jurisdiction, list-type and risk-tier thresholds (F13).
   - A way for a lead to create a rule version.
   - The `source_unavailable` term (§[13]).
   - Most of the operational metrics (alerts per 1,000, time-to-disposition,
     real coverage).
3. **Built, but weaker than the claim.**
   - Claim timestamps are the run start, not the list retrieval time.
   - "Reproducibility" is measured as a JSON round-trip on the corpus, not as a
     replay of stored decisions.
   - Replay ignores `normalizer_version` drift.
   - Hard-identifier weights don't all outrank fuzzy-name weights.
4. **In neither the PRD nor the build.**
   - A frozen regression suite of confirmed failures.
   - A cadence for regenerating the corpus.
   - Diacritic, particle and shell-alias corpus categories.

---

## [2] Traceability matrix

### [2.1] Stage 1 — Extraction

| Requirement (brief) | PRD | Build | Gap |
|---|---|---|---|
| Model turns unstructured sources into typed claims | ✅ F1, §[4] | ⏸ C3: deterministic v1. Structured lists are parsed by code (`app/lists/persons.py`, OFAC remarks included). No LLM | None for v1. Phase 3/4 icebox (IS3-*, IS4-*) |
| Every claim carries source, timestamp, locator | ✅ F2 | ◐ `screening_claim` has `source`, `locator` (`source:entry@snapshot#field`) and `retrieved_at`. **But `retrieved_at` is set to the run start** (`stages.py` `now = run.started_at`), not the snapshot's `retrieved_at` (inferred) | **G1** |
| A claim without a source is thrown out | ✅ F2 | ◐ `source`/`locator` are NOT NULL, so an unsourced claim fails at the DB with an error. Nothing discards it quietly. All claims come from structured lists today, so this can't happen yet | Covered by G1's test. Revisit when extraction lands |
| Extractor never sees the decision rule | ✅ F3 | ⏸ No extractor yet | Carry forward as an IS3/IS4 acceptance criterion |
| Claim types: legal/trading names, registration numbers, jurisdiction, incorporation date, officers, ownership chain, status | ❌ PRD-IDV lists **person** attributes (DOB, POB, nationality, IDs, roles). Corporate attributes belong to the KYB PRD | ❌ Person records only (`parse_*_individuals`, `sdn_type == individual`) | **G2: scope decision (D1)** |

### [2.2] Stage 2 — Blocking

| Requirement | PRD | Build | Gap |
|---|---|---|---|
| Cut the list to a small candidate set, tuned only for recall | ✅ F4–F5 | ✅ `BlockingIndex`: full-token matches are never capped; only partial matches are capped at 200 | Partial-match cap only covered by the corpus gate. Covered by G11 |
| A dropped candidate can't be recovered, so this is the riskiest stage | ✅ F5, §[10] | ✅ CI gate `blocking_recall = 1.0` (`metrics.py` GATES) | — |
| Transliteration | ✅ F6 | ✅ anyascii + Metaphone + skeleton. Mohammed/Muhammad/Mohamed share keys (**verified**) | — |
| Reversed name order | ✅ F6 | ✅ Keys are per token and order-free. `name_token_reordered` term | — |
| Patronymics and particles | ✅ F6 | ✅ Particle list (`al`, `bin`, `van`, `von`, …). One unmatched record token allowed in scoring | Corpus has no particle-only cases (**G12**) |
| Accents / diacritics | ✅ F6 | ✅ Müller/Mueller, José/Jose share keys (**verified**) | **The corpus has zero non-ASCII subject names** (**verified**). Untested at the gate (**G12**) |
| Legal suffixes | ❌ Not in PRD-IDV (people have none) | ❌ No suffix handling in `names.py`. KYB's OFAC adapter strips suffixes for company names only | **G2 / D1** |
| Initials | ✅ F6 | ✅ Initials produce no keys; `name_initials_compatible` | — |

### [2.3] Stage 3 — Scoring

| Requirement | PRD | Build | Gap |
|---|---|---|---|
| Only named terms with declared weights | ✅ F7, §[13] | ✅ `TERM_CATALOG`; weights in `rule_version.config` | — |
| Each term has ≥1 piece of evidence | ✅ F7 | ✅ `ScreeningTerm.claim_ids` validator, subject + record claim per term | — |
| Hard identifiers (national ID, reg no, exact DOB) outrank similar-looking names | ✅ F8 ("ID or full DOB outranks a matching surname") | ◐ `id_number_match` 0.6 is the top weight. **But `dob_full_match` 0.3 < `name_translit_equivalent` 0.4 / `name_token_reordered` 0.45.** It meets the PRD's "outranks a surname" (`name_partial_overlap` 0.2), not the brief's "outranks similar-looking names" | **G4** (D4: new rule version) |
| A missing source lowers confidence and is never "no risk" | ✅ F9, N4 | ◐ Guarded auto-CLEAR: a missing or stale required list turns CLEAR into REVIEW. **But the PRD §[13] `source_unavailable` term doesn't exist**, and no confidence value is exposed | **G5** |
| Conflicting evidence is shown, not averaged away | ✅ F10 | ◐ Conflicts are separate negative terms, and a name + conflict is floored at REVIEW. They're still *summed* into the score. The UI marks them red, but the queue has no "has conflict" flag, and `nationality_conflict` doesn't floor | **G6** |

### [2.4] Stage 4 — Disposition

| Requirement | PRD | Build | Gap |
|---|---|---|---|
| CLEAR / REVIEW / MATCH, with REVIEW as abstention | ✅ F11 | ✅ `dispose.decide` | — |
| Advisory; never auto-clears a possible sanctions hit | ✅ F12, C2 | ✅ Guarded auto-CLEAR only when every candidate is below `clear_below`, no conflict floor applies, and every list is present and fresh | — |
| Thresholds settable per jurisdiction | ✅ F13 (list type, jurisdiction, customer risk tier) | ❌ One global `thresholds` dict per rule version. Subjects carry no jurisdiction or risk-tier field to select on | **G7** (D3) |
| Changed without re-running extraction | ✅ F13 | ◐ `rules.new_rule_version()` exists, but **no API, CLI or UI calls it**. A threshold change needs a Python shell. The open `match_at` 0.9 → 0.8 question (implementation-notes 2026-09-24) is blocked on this | **G8** |

### [2.5] Stage 5 — Audit

| Requirement | PRD | Build | Gap |
|---|---|---|---|
| Append-only in code and in the DB | ✅ F14, C4 | ✅ IS1-T4 triggers on `audit_event`, `screening_decision`, `screening_disposition` | — |
| Stores full frozen input, rule version, every term, threshold, verdict | ✅ F15 | ✅ Encrypted frozen bundle, `rule_version_id`, `terms`, `thresholds`, `system_disposition`, `snapshot_ids`, `normalizer_version` | — |
| Replay gives the same verdict after sources change | ✅ F16, N1 | ◐ Replay is pure over the frozen bundle, and the test deletes the list rows and blocks sockets. **But replay runs *today's* `decide`/`score_pair`/normalizer code.** `normalizer_version` is recorded but never checked, so a code change (n2 → n3) can silently change old verdicts. Nothing in CI replays stored historical decisions | **G9** |

### [2.6] Optional judgment model

| Requirement | PRD | Build | Gap |
|---|---|---|---|
| Calibrated model for meaning-level matches; output is evidence, persisted for replay | ✅ §[9], C3 | ⏸ IS4-T1/T2 icebox | None for v1 |

### [2.7] Eval characteristics

| Metric | PRD §[7] | Build (`metrics.py`, CI artifact) | Gap |
|---|---|---|---|
| FP rate at 100% recall (headline) | ✅ | ✅ `fp_rate_at_full_recall` (corpus) | — |
| Alerts per 1,000 onboardings | ✅ | ❌ | **G10** |
| REVIEW rate | ✅ | ◐ `abstention_rate` (corpus; 0.78). No alert when it's too low or too high | **G10** |
| Replay reproducibility (target 100%) | ✅ | ◐ Corpus bundles `decide` → JSON round-trip → `decide`. That tests JSON stability, not replay of stored decisions | **G9** |
| Time-to-disposition | ✅ | ❌ (derivable: `screening_disposition.created_at − screening_decision.created_at`) | **G10** |
| Coverage | ✅ | ◐ Hard-coded `source_status` in the corpus run, so it's always 1.0. Production coverage isn't reported | **G10** |
| Blocking recall as its own release gate | ✅ | ✅ CI gate | — |
| F1 / accuracy rejected | ✅ | ✅ Not computed | — |

### [2.8] Adversarial eval set

| Case type (brief) | PRD §[7] | Corpus v2 (90 cases) | Gap |
|---|---|---|---|
| 1. Transliteration variants | ✅ | ✅ 10 | — |
| 2. Reversed name order | ✅ | ✅ 10 (`inversion`) | — |
| 3. Patronymics and particles | ◐ PRD names patronymics; particles only in F6 | ◐ 10 patronymic; particles appear only incidentally | **G12** |
| 4. Accents / diacritics | ◐ F6 only, not in the §[7] corpus list | ❌ Zero non-ASCII subject names | **G12** |
| 5. Corporate-suffix noise | ❌ | ❌ | **D1** |
| 6. Shell-alias chains | ❌ | ❌ | **D1** (for people: alias-of-alias chains, G12) |
| 7. Very common names | ✅ | ✅ 10 (`common_name_cluster`) | — |
| Generated against OFAC SDN and OpenSanctions | ❌ PRD says synthetic fictional | ❌ Fictional only | **D2** |
| Confirmed failures frozen into a CI regression suite | ❌ | ❌ | **G11** |
| Regenerated regularly (evaders learn thresholds) | ◐ §[7]/§[10] say so; cadence is open question Q4 | ❌ One deterministic generator, run by hand | **G13** |

---

## [3] Gap register

| ID | Gap | Class | Size | Risk if left |
|---|---|---|---|---|
| G1 | Claim `retrieved_at` is the run start, not the list snapshot's retrieval time | Build bug vs F2 | S | The evidence citation shows the wrong "as of" date. An examiner can't tie a claim to the list version from the claim alone |
| G2 | Corporate/entity screening (entity list records, legal suffixes, registration numbers, shell-alias chains) | Spec scope | L | The brief's corporate cases aren't screened by this engine. UN/EU/UK *entity* records aren't screened by anything (KYB covers OFAC company names only) |
| G4 | `dob_full_match` weight is below some fuzzy-name weights | Rule config vs brief | S | The rule contradicts the brief's corroboration-first wording. Changing it shifts dispositions (new rule version) |
| G5 | No `source_unavailable` term; no confidence output | Build vs PRD §[13] | S | The gap shows only as a REVIEW floor, not as a named, explainable term |
| G6 | Conflicts not flagged at run/queue level; `nationality_conflict` doesn't floor | Build vs F10 | S | Analysts can't triage "has conflicting evidence" from the queue |
| G7 | Thresholds are global only (no list type / jurisdiction / risk tier) | Build vs F13 | M | Can't tune per market. Every tuning change hits every subject |
| G8 | No lead-facing way to create a rule version | Build vs F13 | S–M | Threshold changes need an engineer; the `match_at` decision stays stuck |
| G9 | Replay uses current code; `normalizer_version` never checked; no stored-decision replay in CI | Build vs F16/N1 | M | A future normalizer or scoring change can silently change historical verdicts, and nothing catches it |
| G10 | Operational metrics missing (alerts/1k, time-to-disposition, real coverage, REVIEW-rate band) | Build vs §[7] | M | Can't show the buyer's own units or spot automation bias (§[10]) |
| G11 | No frozen regression suite of confirmed failures | Neither | S | A fixed miss can come back unnoticed |
| G12 | Corpus lacks diacritic, particle and alias-chain categories | PRD §[7] partial | S | F6 behavior that works today (verified) isn't gated, so a regression would pass CI |
| G13 | No corpus regeneration cadence or variation | PRD Q4 open | S–M | Thresholds get tuned to a fixed corpus that evaders can learn |

---

## [4] Decisions needed before building

| # | Decision | Options | Recommendation | Cost of getting it wrong |
|---|---|---|---|---|
| D1 | Does this engine screen **entities** too (the corporate items in the brief)? | (a) Yes: add entity records + suffix/registration-number handling to `app/screening` as a subject type. (b) No: fold entity-list screening into KYB's sanctions adapter (UN/EU/UK entities, suffix noise). (c) Defer | **(b) for now, file (a) as `future`.** PRD-IDV is scoped to people. KYB already has a suffix-stripping sanctions stage that lacks the UN/EU/UK entity lists. Needs a PRD amendment either way | Wrong scope means rework of the subject model, since the schema is person-shaped |
| D2 | May eval cases be **derived from real OFAC/OpenSanctions entries**? | (a) Fictional-only (today). (b) Runtime-derived variants of real public list entries, never committed, run in a scheduled job. (c) Commit them | **(b).** Keeps the repo rule (no real person data committed) and still exercises real name shapes. OpenSanctions is licensed data (C7), so start with the official lists only | (c) conflicts with the repo's data rule and OpenSanctions licensing |
| D3 | Threshold **selectors**: which attributes pick a threshold set? | list type · subject jurisdiction (residence/nationality) · customer risk tier (new optional intake field) · the integrating client | **List type + customer risk tier + client jurisdiction (per API client/tenant)** first. Subject nationality as a selector risks disparate treatment | API/schema shape. A new intake field is hard to remove later |
| D4 | Re-weight so every hard identifier outranks every fuzzy name term? | (a) Yes: `dob_full_match` ≥ 0.45, retune thresholds. (b) Keep; amend the brief to the PRD's "outranks a surname" | **Decide together with the `match_at` question** (implementation-notes 2026-09-24), in one rule version, measured on the corpus | Changes live dispositions. Needs the corpus report before and after |

---

## [5] Implementation Units

Ordered by risk reduction. Each ticket is test-first per CLAUDE.md §[2], with one
commit per ticket and a human merge.

**Phase 6a — Close correctness gaps (no owner decision needed)**

### U1. IS6-T1 — Claim timestamps come from the snapshot (G1)

- Record claims take `retrieved_at` from their `ListSnapshot`. Subject claims
  keep the submission time.
- RED test: a snapshot retrieved yesterday plus a run today; the claim must
  equal the snapshot time.
- No migration. Old rows are left as they are (append-only), and the evidence
  panel (PLAN-IS-EVIDENCE) derives the snapshot time live.

### U2. IS6-T2 — Replay guards against code drift (G9)

- Replay compares the decision's `normalizer_version` (and a new
  `SCORING_VERSION` constant) with the current ones. On mismatch it returns
  `reproduced: null, reason: "code_version_changed"` and never reports a
  silent pass or fail.
- Add **golden decisions**: 30 frozen bundles plus expected verdicts, checked
  in (fictional).
- A CI test replays all of them. Changing a verdict needs a deliberate fixture
  update and a version bump.
- Replace the JSON round-trip "reproducibility" metric with golden-replay
  reproducibility.

### U3. IS6-T3 — `source_unavailable` term (G5)

- When a required list is missing or stale, each candidate (and a synthetic
  "coverage" row when there are no candidates) gets a named term that cites the
  snapshot or its absence.
- Weight 0 on score, effect = floor at REVIEW. This keeps today's behavior,
  now named.
- Rules that predate the term replay unchanged (the same pattern as
  `dob_partial_conflict`).

### U4. IS6-T4 — Conflict flag (G6)

- `decide` returns `has_conflict` per candidate and per run.
- The queue gets a "Conflicting evidence" filter/badge.
- Decide in the ticket whether `nationality_conflict` should floor (default:
  no, because nationality data is noisy; note it).

### U5. IS6-T5 — Corpus categories and regression suite (G11, G12)

- Add `diacritics`, `particles`, `alias_chain` (person alias-of-alias) and
  `original_script_subject` categories. `alias_chain` is a subject that matches
  only an alias the record carries through a second alias.
- Add `tests/screening/regressions/`: one JSON case per confirmed miss or false
  positive, with a free-text `found_in` field. The recall gate and the
  per-case expected band run in CI. It's never regenerated, only appended.
- Document the "confirmed failure → regression case" step in the runbook.

**Phase 6b — Rules and thresholds (after D3, D4)**

### U6. IS6-T6 — Rule-version admin (G8)

- `POST /screenings/rules` (lead only, audited, append-only), with a dry run
  that returns the corpus metrics report for the proposed config next to the
  current one.
- `GET /screenings/rules`.
- A minimal lead UI form (JSON editor + diff + metrics comparison).

### U7. IS6-T7 — Selector-based thresholds (G7)

- Rule config grows `threshold_sets: [{when: {list_type?, risk_tier?, client_jurisdiction?}, thresholds}]`
  with a default. The most specific match wins, and the tie-break is recorded.
- The decision stores the selected set and why it was selected.
- Optional `risk_tier` intake field (D3).
- Old rule configs (no `threshold_sets`) replay unchanged.

### U8. IS6-T8 — Rule version 2 (G4 + `match_at`)

- One new rule version, created through IS6-T6, with the corpus report before
  and after attached to the ticket.
- Owner approves the numbers before it becomes current.

**Phase 6c — Metrics and adversarial refresh**

### U9. IS6-T9 — Operational metrics (G10)

- Lead-only `GET /screenings/metrics?from&to`, derived live from decisions,
  dispositions and runs. No new table (prior: derive, don't store).
- It reports:
  - alerts per 1,000 screenings;
  - REVIEW rate with a configurable healthy band and an out-of-band warning;
  - time-to-disposition p50/p95;
  - coverage (share of runs where every required list was complete);
  - replay reproducibility over a sampled window.
- Every aggregate also reports its per-rule-version breakdown.
- Small lead panel on the Individuals page.

### U10. IS6-T10 — Scheduled adversarial refresh (G13, D2)

- A seeded variant generator (transliteration tables, order swaps, particle
  insertion/removal, diacritic folding and adding, alias chaining) with the
  seed as a parameter.
- The weekly scheduled job generates variants of **fictional** records
  (committed) and, if D2 = (b), of **live official-list entries** at runtime
  (not committed). It reports recall and FP@100%-recall per category.
- Any recall miss files a ticket plus a regression case (IS6-T5).
- Resolves PRD Q4 (cadence = weekly, owner-adjustable).

**Phase 6d — Scope (after D1)**

### U11. IS6-T11 — Entity-list coverage

- Per D1 (b): extend the KYB sanctions stage to UN/EU/UK entity records with
  suffix normalization (Ltd/LLC/GmbH/OOO/…) and registration-number
  corroboration.
- Add corporate-suffix and shell-alias corpus cases to the KYB sanctions tests.
- PRD amendments first: PRD.md for KYB, and PRD-IDV §[8] to state that
  entities are out of scope.

### Docs

- Amend PRD-IDV:
  - §[7]: add the diacritic, particle and alias-chain categories, the regression
    suite, the cadence answer to Q4, and D2's runtime-derived eval.
  - §[13]: `source_unavailable` semantics.
  - F13: selector list per D3.
- Add a Phase 6 section and a Current Status update to
  `BUILD_PLAN-individual-screening.md`.
- Add implementation-notes entries per ticket.

### Dependency order

1. IS6-T1, IS6-T3, IS6-T4, IS6-T5 (parallel; no decisions)
2. IS6-T2 (after T3, so the golden fixtures include the new term)
3. D3, D4 → IS6-T6 → IS6-T7 → IS6-T8
4. IS6-T9 (after T2 for replay sampling), IS6-T10 (after T5; D2)
5. D1 → IS6-T11

### Validation per ticket

- Backend gate: `ruff check`, `ruff format --check`, `pytest -q`.
- Frontend gate: `npm run lint && npm run test && npm run build`.
- The `screening-metrics` CI gate stays green.
- Any rule change attaches before/after metrics.
