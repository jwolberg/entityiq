---
anchor: PLAN-IS-EVIDENCE
title: Decision evidence panel — pull-out sidebar for individual screening
status: draft
date: 2026-09-25
---

# Decision evidence panel (pull-out sidebar)

## [1] User-facing goal

An analyst, lead or examiner on an Individual screening detail page opens a
**"Why this decision?"** sidebar. It walks through **every decision the system
made, in order**, and each step **cites the sources behind it**:

- which lists were checked;
- why each candidate was pulled in;
- how each term scored;
- why each candidate landed in its band;
- why the run is CLEAR, REVIEW or MATCH (and whether it auto-closed);
- what humans decided afterwards.

This serves PRD-IDV **N2** ("why a decision was made, in one screen"), **F7**
(every term cites evidence) and **F15/F16** (what was known at the time).
Success means an examiner can defend a disposition from the panel alone, without
opening the database or the list files.

**Not in scope:** editing anything from the panel, KYB company pages (see §[7]),
and model or adverse-media evidence (iceboxed Phase 3/4).

## [2] What exists today (inferred from code)

- **`GET /screenings/{run_id}`** (`app/screening/api.py`) already returns:
  - candidates with `blocking_keys`, `score`, `band`, and `terms` (name, weight,
    record value, `claim_ids`);
  - `claims` with `source`, `locator` and `retrieved_at`;
  - the decision's `thresholds`, `rule_version`, `snapshot_ids`,
    `normalizer_version` and `auto_closed`;
  - `run.stages` coverage and human dispositions.
- **`IndividualDetail.tsx`** renders terms and raw locator strings inline per
  candidate.
- **Missing: the *reasons*.**
  - `dispose.decide` returns bands but not *why*: threshold crossed vs conflict
    floor, most-severe rollup, auto-CLEAR guard outcome, common-name frequency.
  - Blocking doesn't say whether a candidate was a full-token match (never
    capped) or a partial one.
  - Citations are opaque strings: `ofac_sdn:1234@<uuid>#dobs`. They carry no
    list name, retrieval date, content hash or link.
- No drawer component exists. The UI uses inline style objects and no component
  library. `ActivityPanel` is the closest pattern (it fetches its own data and
  fails soft).

## [3] Design

### [3.1] Derive, don't store

Every reason can be **derived from what's already stored**. Nothing new is
persisted, and nothing is decrypted:

| Step | Derived from |
|---|---|
| Sources checked | `decision.snapshot_ids` → `list_snapshot` (source, `retrieved_at`, `content_sha256`, `record_count`); `run.source_availability`; freshness vs `ENTITYIQ_SCREENING_MAX_LIST_AGE_DAYS`; `SOURCES[...].url` |
| Why each candidate was pulled in | `candidate.blocking_keys` + record names (re-run `name_keys` on the *record* side only, no subject PII) → full vs partial match |
| Term scoring | `decision.terms` (weights as frozen) + `screening_term.claim_ids` → claims |
| Band per candidate | `score`, term names, `decision.thresholds`: the same rule as `dispose._band` |
| Run disposition | Candidate bands (most severe) + `source_status` → auto-CLEAR guard |
| Common-name penalty | `common_name_penalty.record_value` (frequency) vs rule `common_name_threshold` |
| Human decisions / replay | `screening_disposition` rows; `screening.replayed` audit events |

This follows the standing prior: store only what can't be derived. It also means
the panel **still works after a subject is crypto-shredded**. Subject *values*
are the only thing lost, and the detail page already handles that.

**Single source of truth for the rules:** refactor `dispose._band` into
`band_with_reason(score, terms, thresholds) -> (band, reason_code)`. Both
`decide()` and the explanation call it, so the explanation can't drift from the
verdict.

- The reason codes are `score_at_or_above_match`, `score_below_clear`,
  `between_thresholds` and `conflict_floor`.
- The run level gets `rollup_most_severe`, `auto_clear_allowed`,
  `auto_clear_blocked_by_coverage` and `no_candidates`.

### [3.2] API: `GET /screenings/{run_id}/explanation`

A separate endpoint, so the detail payload and its query-count test stay
unchanged and the panel loads lazily.

```
{
  "run_id", "rule_version", "normalizer_version", "decided_at",
  "steps": [
    {"kind": "sources", "lists": [{source, display_name, snapshot_id,
        retrieved_at, content_sha256, record_count, status, fresh,
        max_age_days, source_url}]},
    {"kind": "blocking", "candidate_count", "cap", "candidates":
        [{candidate_id, record_ref, match: "full"|"partial", matched_keys}]},
    {"kind": "scoring", "candidates": [{candidate_id, record_ref, score,
        terms: [{name, label, weight, direction, record_value,
                 citations: [citation]}]}]},
    {"kind": "banding", "thresholds", "candidates": [{candidate_id, score,
        band, reason_code, reason_text}]},
    {"kind": "disposition", "system_disposition", "auto_closed",
        reason_code, reason_text, "coverage_gaps": [...],
        "common_name": {frequency, threshold, applied} | null},
    {"kind": "human", "dispositions": [...], "replays": [...]}
  ],
  "citations": {
    "<claim_id>": {
      "about": "subject"|"record",
      "source": "ofac_sdn",
      "display_name": "OFAC SDN List",
      "entry_id": "1234",
      "field": "dobs",
      "snapshot_id": "...",
      "snapshot_retrieved_at": "...",
      "content_sha256": "...",
      "source_url": "...",
      "locator": "<raw>"
    }
  }
}
```

- **Citation metadata:**
  - A `SOURCE_META` map beside `ingest.SOURCES` supplies the display name,
    publisher and file URL.
  - Per-entry deep links are added **only** where the publisher has a stable
    one. They're verified during IS7-T2 rather than guessed. Otherwise the panel
    shows file URL + entry id.
  - Snapshot time comes from the snapshot row, not `claim.retrieved_at`. That
    fixes older rows too (gap G1 in PLAN-IS-GAPS).
- **Subject-side citations** say "Submitted by <API client / operator> at
  <time>" and carry no values. The panel shows subject values from the detail
  payload it already has, when PII is present.
- **`reason_text`** is plain English generated from the code and numbers, for
  example: *"Score 0.80 is between the clear (0.35) and match (0.90) thresholds
  → REVIEW."* It's deterministic and needs no model.
- **Auth and scoping:** same as the detail endpoint. Operators, leads and
  examiners see any run; an integration key sees only its own runs (404
  otherwise).
  - Human-disposition notes and operator ids are shown to operators only, as in
    the detail endpoint.
  - The read is audited as `screening.explanation_viewed`.
- **No new dependencies, no migration.**

### [3.3] UI: `DecisionEvidencePanel`

- **Entry points.**
  - A **"Why this decision?"** button next to the disposition badge in the
    `IndividualDetail` header.
  - A **"Why?"** link on each candidate card, which opens the panel scrolled to
    that candidate's scoring and banding steps.
- **Shell.**
  - A right-side drawer, 440px on desktop and full-width under 720px.
  - `role="dialog"`, `aria-modal="false"` so the page stays readable beside it,
    and `aria-labelledby` pointing at its title.
  - Esc and a close button dismiss it. Focus moves into the drawer on open and
    returns to the trigger on close.
  - It's not a modal: the analyst can still use the disposition form with the
    panel open.
- **Body.** A numbered timeline of the steps in §[3.2]. Each step shows a
  one-line conclusion first, then an expandable breakdown. Each citation renders
  as a chip:
  - chip text: `OFAC SDN · entry 1234 · dobs · list as of 2026-09-20`;
  - it expands to show the snapshot hash (short, with copy), the record count,
    and a link to the source file or entry;
  - the chip's accessible label spells out the same information.
- **States.**
  - Loading.
  - Fail-soft error ("Decision explanation is unavailable right now"), as in
    `ActivityPanel`. The rest of the page is unaffected.
  - Run still in flight: "Decision not made yet". It refreshes on the page's
    existing poll tick.
  - Subject shredded: steps render, subject values show "shredded".
  - No candidates: the blocking step reads "No list record shared a name key
    with the subject".
- **Deep link.** The `?why=1&candidate=<id>` query param opens the panel, so an
  examiner can share a link to a specific explanation.
- **Style.** Inline style objects and existing colors, with conflict red and
  support green reused from `IndividualDetail`.

## [4] Tickets (`IS7-*`)

Each ticket is test-first (RED test reviewed before implementation), with one
commit per ticket and a human merge.

- **IS7-T1: Band reasons in `dispose` (backend, S).**
  - Extract `band_with_reason`; `decide()` uses it; add
    `run_reason(results, source_status)`.
  - RED:
    - for every corpus case, `decide()` output is byte-identical before and
      after the refactor (snapshot the current results first);
    - each reason code has a test (one per tier, per CLAUDE.md §[12.1]);
    - golden replay stays green.
- **IS7-T2: Explanation builder and endpoint (backend, M).**
  - `app/screening/explain.py` (pure: stored rows in, dict out) plus
    `GET /screenings/{run_id}/explanation`, and `SOURCE_META` with verified
    URLs.
  - RED:
    - for every corpus case run through the real pipeline in the e2e fixture,
      each banding step's `band` equals the stored band, and the disposition
      step equals `system_disposition`;
    - the payload contains **no subject values**: assert that the subject's
      name, DOB and document numbers never appear in the serialized JSON;
    - it works after crypto-shred;
    - an unavailable or stale list gives `auto_clear_blocked_by_coverage`;
    - the common-name step appears when the penalty applied;
    - an integration key reading another client's run gets 404;
    - an examiner can read; the audit event is written;
    - the query count is constant in the number of candidates (existing pattern).
- **IS7-T3: Client types and API method (frontend, S).** Add
  `ScreeningExplanation` types to `api/client.ts` and a `getScreeningExplanation`
  method.
- **IS7-T4: `DecisionEvidencePanel` component (frontend, M).**
  - Vitest RED:
    - opens from the header button and the per-candidate link, scrolled to that
      candidate;
    - Esc closes it and focus returns to the trigger;
    - citation chips render the list name, entry, field and as-of date;
    - error state is fail-soft;
    - in-flight and shredded states render;
    - the deep-link param opens the panel;
    - a CLEAR auto-closed run shows the auto-CLEAR reason.
- **IS7-T5: Wire into `IndividualDetail` + UX pass (frontend, S).**
  - Remove the now-duplicated raw locator lines from the candidate cards, or
    keep a one-line summary.
  - Run the `ux-designer` agent on the panel and fix its findings.
  - Check it in the demo (`./scripts/demo.sh`) with all three dispositions and a
    monitoring run.

**Order:** T1 → T2 → T3 → T4 → T5. T3 can start once T2's response schema is
fixed.

**Relation to PLAN-IS-GAPS:**

- This plan doesn't need any Phase 6 ticket first.
- If IS6-T3 (`source_unavailable` term) or IS6-T4 (conflict flag) land first,
  the panel shows them for free, because they're just terms and reason codes.
- IS6-T7 (threshold selectors) adds a "threshold set selected because …" line to
  the banding step, a one-field extension.

## [5] Risks and tradeoffs

- **Explanation drifting from the verdict.** Mitigated by sharing
  `band_with_reason` with `decide()`, plus the corpus-wide equality test in
  IS7-T2.
- **Deriving live vs storing a trace.** Deriving keeps one source of truth and
  works for every existing decision. The cost is a few extra queries per panel
  open. The lazy endpoint keeps the detail page's cost unchanged.
- **Reasons derived from today's code for old decisions.** If the band rules
  change, an old decision would be explained with new rules.
  - IS6-T2's code-version guard covers this. When the decision's
    normalizer/scoring version differs from the current one, the panel shows a
    banner: "explained with current rules; decision made under <version>".
  - Until IS6-T2 lands, versions are all `n2`, so there's no drift yet.
- **PII leakage through the new endpoint.** Covered by the no-subject-values
  test and by reusing the detail endpoint's scoping.
- **Per-entry source links.** Official list sites change URLs. Links are
  best-effort; the durable citation is source + entry id + snapshot hash.

## [6] Validation

- Backend: `ruff check . && ruff format --check . && pytest -q` (the new tests
  in `tests/screening/test_explain.py`).
- Frontend: `npm run lint && npm run test && npm run build`.
- Manual: demo stack, open the panel on the CLEAR, REVIEW, MATCH and monitoring
  runs. Screenshots go in the PR.

## [7] Later (file as `horizon: future`)

- Reuse the drawer shell on KYB `CompanyDetail`, fed by the KYB
  signals/evidence rows. Same UX, different step list.
- "Export explanation as PDF" for examiner packets.
- Show the Phase 4 model term's probability and model version as a citation once
  IS4 lands.
