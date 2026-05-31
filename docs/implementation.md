# Implementation

## Scope Implemented
- Requested scope: Full operator actions (re-run analysis, correct submitted data
  + re-run, add notes, export report) and dashboard filters/search.
- Related phase: Phase 2 — Deepen the Tracks (Track: Operator workbench)
- Related ticket(s): **P2-T10 — Full operator actions + dashboard filters**

## Approach
- High-level strategy: **Frontend-only.** The backend endpoints already exist from
  P2-T11 (`POST /reanalysis/{run_id}`, `POST /workflow/runs/{run_id}/correct`,
  `POST /workflow/runs/{run_id}/notes`, `GET /reports/{run_id}/export`). This ticket
  adds typed client methods and the operator UI that drives them, plus client-side
  dashboard filtering. No backend/schema/pipeline changes.
- Key decisions:
  - New `components/OperatorActions.tsx` holds the four run-level actions, keeping
    `CompanyDetail` focused on display. Mirrors the existing presentational-
    component split (`RegistrationDiff`, `DetailPanels`).
  - Correct-and-re-run sends **only changed fields**: the form prefills from the
    report's mismatches (submitted values) and diffs against them on submit, so the
    backend receives a minimal `corrections` map (matching its "only listed fields
    are updated" semantics). Empty submission is blocked client-side.
  - Re-run and correct-and-re-run both create a superseding run; on success the UI
    surfaces the new `run_id` and offers `onOpenRun` to navigate to it. `App` passes
    `key={runId}` so the detail view cleanly remounts/reloads for the new run.
  - Export downloads the `/export` JSON as a file via a Blob + anchor, guarded for
    environments without `URL.createObjectURL`.
  - Dashboard filters (search over company/domain, review-status, risk-band) are
    **client-side** over the already-fetched list — smallest change that delivers
    the value for the current list size; no new query params on the API.
- Assumptions:
  - Correctable field set matches the backend `_CORRECTABLE_FIELDS` (encoded as the
    `CorrectableField` union in `client.ts`).
  - Risk bands follow the existing dashboard color thresholds (low < 40 ≤ medium
    < 70 ≤ high).

---

## Implementation Plan
1. `api/client.ts` — add `triggerReanalysis`, `correctAndRerun`, `addNotes`,
   `exportReport` + request/response types and the `CorrectableField` union.
2. `components/OperatorActions.tsx` (new) — re-run, correct-and-re-run form,
   add-notes, export (JSON download).
3. `pages/CompanyDetail.tsx` — render `OperatorActions`; add `onOpenRun` prop;
   derive prefill submitted values from mismatches.
4. `App.tsx` — wire `onOpenRun` to navigation; remount detail via `key`.
5. `pages/Dashboard.tsx` — search + review-status + risk-band filters, filtered
   count, and a no-matches state.
6. Tests — Dashboard filter test; new `OperatorActions.test.tsx`.

Files to create: `components/OperatorActions.tsx`, `components/OperatorActions.test.tsx`
Files to modify: `api/client.ts`, `pages/CompanyDetail.tsx`, `App.tsx`,
`pages/Dashboard.tsx`, `pages/Dashboard.test.tsx`

---

## Code Changes

### File: frontend/src/components/OperatorActions.tsx  (new)
- Change summary: Operator workbench actions for a run — Re-run Analysis,
  Correct Data & Re-run (expandable form, sends only changed fields), Add Note,
  and Export Report (JSON download). Surfaces the new superseding run id with an
  optional "View new run" link.

### File: frontend/src/api/client.ts
- Change summary: Added `ReanalysisResponse`, `CorrectableField`,
  `CorrectAndRerunRequest/Response`, `AddNotesRequest/Response`, and four client
  methods (`triggerReanalysis`, `correctAndRerun`, `addNotes`, `exportReport`).

### File: frontend/src/pages/CompanyDetail.tsx
- Change summary: Added an "Operator Actions" section rendering `OperatorActions`;
  new `onOpenRun?` prop; `submittedValuesFromReport()` derives correction-form
  prefills from the report's mismatches.

### File: frontend/src/App.tsx
- Change summary: Passes `onOpenRun` (navigates to the run) and `key={runId}` so
  navigating to a superseding run remounts the detail view.

### File: frontend/src/pages/Dashboard.tsx
- Change summary: Added a filter bar (search over company/domain, review-status
  select, risk-band select), client-side filtering, a filtered/total count, and a
  "no matches" state; table now renders the filtered list.

### Files: frontend/src/components/OperatorActions.test.tsx (new),
### frontend/src/pages/Dashboard.test.tsx
- Change summary: New tests for re-run, correct-and-re-run (only-changed-fields +
  empty-block), add-notes, and export; new Dashboard test for search + status +
  risk filtering and the no-matches state.

---

## Acceptance Criteria Mapping
PRD § Operator Actions + § Dashboard (filters/search):

- Criterion: **Mark reviewed** — already shipped (P1-T10); unchanged.
- Criterion: **Re-run analysis** — `OperatorActions` Re-run button →
  `POST /reanalysis/{run_id}`; surfaces the superseding run.
  - File(s): `components/OperatorActions.tsx`, `api/client.ts`
- Criterion: **Correct submitted data (+ re-run)** — correction form →
  `POST /workflow/runs/{run_id}/correct` with only changed fields.
  - File(s): `components/OperatorActions.tsx`, `pages/CompanyDetail.tsx`
- Criterion: **Add review notes** — note field → `POST /workflow/runs/{run_id}/notes`.
  - File(s): `components/OperatorActions.tsx`
- Criterion: **Export report** — `GET /reports/{run_id}/export` downloaded as JSON.
  - File(s): `components/OperatorActions.tsx`, `api/client.ts`
- Criterion: **Dashboard filters/search** — search + review-status + risk-band
  filters with filtered count and no-matches state.
  - File(s): `pages/Dashboard.tsx`

All write actions are audited server-side (existing P2-T11 endpoints).

---

## Build Plan Mapping
- Ticket: **P2-T10 — Full operator actions + dashboard filters**
  - Status: **Complete**
  - What was completed: All operator actions (re-run, correct + re-run, notes,
    export) wired to the existing P2-T11 endpoints, plus dashboard search/filters.
    Lint + tests green.
  - Remaining work: None for this ticket. P2-T9 (HQ map) remains blocked on Open
    Decision #4; P2-T12 (API auth for integrating systems) is independent and next.

---

## Validation
- How the feature was tested:
  - `OperatorActions.test.tsx`: re-run posts to `/reanalysis` and exposes the new
    run id (+ onOpenRun); correct-and-re-run posts only changed fields to
    `/workflow/.../correct`; empty correction is blocked with no fetch; add-note
    posts to `/workflow/.../notes` and confirms; export hits
    `/reports/{run_id}/export` and triggers the download anchor.
  - `Dashboard.test.tsx`: search narrows results, review-status and risk-band
    filters select the right rows, and an impossible combination shows the
    no-matches notice.
- Lint/test results:
  - `npm run lint` → clean (0/0).
  - `npm test` → **18 passed (4 files)**.
  - `tsc --noEmit`: new files (`OperatorActions.tsx`, `client.ts`) add **zero**
    errors. The only related errors are the pre-existing `auth is possibly null`
    pattern used throughout the app (AuthProvider guarantees non-null); the gate is
    lint + vitest, both green.
- Manual verification steps: `cd frontend && npm run dev`; on the dashboard, use the
  search box and the status/risk dropdowns to filter the queue; open a run → the
  Operator Actions section offers Re-run, Correct Data & Re-run, Add Note, and
  Export Report (JSON). Re-running opens the new superseding run.
- Visible user outcome: Operators can now act on a run end-to-end (correct + re-run,
  re-trigger, note, export) and triage the queue with search + filters — completing
  the operator workbench.

---

## Open Issues
- Known limitations:
  - Dashboard filtering is client-side over the fetched list; server-side
    pagination/filtering can follow if the queue grows large (not required now).
  - Correction-form prefill only covers fields present in the report's mismatches
    (company_name, domain, country, tax_id, billing_address, phone); work_email,
    requester_full_name, linkedin_url start blank (operator enters new values).
  - Export downloads raw JSON; a formatted PDF/HTML export is out of scope.
- Unresolved edge cases: None observed.
- Blockers: None for P2-T10. (Project blockers unchanged: Open Decision #5 Tier-1
  licensing; IPINFO_TOKEN production plan; #4 blocks P2-T9; #7 blocks P3-T3.)

---

## BUILD_PLAN Update
- Current phase: Phase 2 — Deepen the Tracks
- Current ticket: P2-T10 — Complete
- Updated ticket status: P2-T10 → Complete
- Blockers: unchanged (#5 licensing, IPINFO_TOKEN; #4 blocks P2-T9, #7 blocks P3-T3)
- Recommended next ticket: **P2-T12 — API auth for integrating systems**
  (P2-T9 — HQ visualization — remains blocked on Open Decision #4, map provider).
