# Implementation

## Scope Implemented
- Requested scope: P1-T10 — Operator app: list + detail + mark reviewed
- Related phase: Phase 1 — MVP Vertical Slice
- Related ticket(s): P1-T10 (depends on P1-T8, P1-T9)

## Approach
- Added a minimal `GET /reports/` list endpoint to the backend (not in P1-T8).
- Built a typed API client (`frontend/src/api/client.ts`) wrapping all four
  required backend calls: sign-in, list reports, get report, mark reviewed.
- Built an `AuthProvider` + `SignInForm` gating the app behind a session token.
- Built `Dashboard` (list view) and `CompanyDetail` (detail + diff + action) pages.
- Built `RegistrationDiff` component for submitted-vs-discovered comparison.
- Wired everything in `App.tsx` with hash-based routing (no react-router).
- All validation passes: backend ruff + pytest (172 tests), frontend lint + vitest (10 tests).

### Key Decisions
1. **Hash routing over react-router** — two views, no dependency needed.
2. **Token in React state** — avoids XSS persistence; re-sign-in on refresh is
   acceptable for MVP.
3. **N+1 in list endpoint** — queries submission per report row; fine at MVP scale.
4. **`eslint-disable react-refresh/only-export-components`** — AuthContext and
   hooks in one file; suppressed per-file to avoid splitting for no functional gain.

### Assumptions
- Backend auth and reviews endpoints remain unchanged from P1-T9.
- The vite proxy (`/api` → `localhost:8000`) is already configured (confirmed).
- No Tailwind or CSS framework in the project — inline styles used throughout.

---

## Implementation Plan

1. Read all source-of-truth docs and existing backend code (main.py, reports.py,
   submissions.py, operator.py, reviews.py, schemas).
2. Identify gap: no `GET /reports/` list endpoint.
3. Add list endpoint + schema to backend; write 4 backend tests.
4. Run backend ruff + pytest — all green.
5. Create frontend directory structure.
6. Write `api/client.ts` — typed wrappers for sign-in, list, get, mark-reviewed.
7. Write `auth/AuthContext.tsx` — context, hook, sign-in form, provider.
8. Write `components/RegistrationDiff.tsx` — field comparison with status badges.
9. Write `pages/Dashboard.tsx` — table with score/status, loading/empty states.
10. Write `pages/CompanyDetail.tsx` — detail view, diff section, mark-reviewed action.
11. Update `App.tsx` — AuthProvider + AppShell + hash routing.
12. Update `App.test.tsx` (sign-in form rendering).
13. Write `Dashboard.test.tsx` and `CompanyDetail.test.tsx`.
14. Run frontend lint + vitest — all green.
15. Update docs.

---

## Code Changes

### File: `backend/app/schemas/report.py`
- Added `ReportListItemSchema` (thin dashboard row) and `ReportListResponse`.

### File: `backend/app/api/reports.py`
- Added `GET /reports/` route (`list_reports`) returning `ReportListResponse`.
- Resolves company name/domain via submission FK; reads score from JSON summary;
  joins Review table for `review_status`.
- Route placed before `/{run_id}` to avoid path shadowing.

### File: `backend/tests/api/test_reports_list.py` (new)
- 4 tests: empty list, one report no review, reviewed report, no-score pending report.

### File: `frontend/src/api/client.ts` (new)
- Typed interfaces for all API request/response shapes.
- `apiClient` object with `signIn`, `listReports`, `getReport`, `markReviewed`.
- Attaches `Authorization: Bearer <token>` when token is provided.
- All calls use `/api` prefix (proxied by vite).

### File: `frontend/src/auth/AuthContext.tsx` (new)
- `AuthContext`, `useAuth` hook, `SignInForm`, `AuthProvider`.
- Session token in React state (in-memory; cleared on refresh).
- Sign-in form with email/password fields; error + loading states.

### File: `frontend/src/components/RegistrationDiff.tsx` (new)
- Renders mismatches table with per-row match/mismatch/unverified badges.
- Handles `pending` section status (shows notice, no crash).
- Handles empty mismatches list.

### File: `frontend/src/pages/Dashboard.tsx` (new)
- Fetches `GET /reports/` on mount.
- Renders table: company name, domain, analysis date, risk score (color-coded),
  review status badge.
- Loading, empty, and error states.

### File: `frontend/src/pages/CompanyDetail.tsx` (new)
- Fetches `GET /reports/{run_id}` on mount.
- Renders risk score, RegistrationDiff, and mark-reviewed button.
- Mark-reviewed calls `POST /reviews/{run_id}`; success shows a banner; error shown inline.
- Partial reports (pending sections) render without crashing.

### File: `frontend/src/App.tsx` (modified)
- Replaced placeholder with `AuthProvider` + `AppShell`.
- `AppShell` has top nav (logo, operator role, sign out) and hash-based routing.
- Route state: `{ page: "dashboard" }` or `{ page: "detail"; runId }`.

### File: `frontend/src/App.test.tsx` (modified)
- Updated to assert sign-in form renders before auth (not the old plain heading).

### File: `frontend/src/pages/Dashboard.test.tsx` (new)
- 4 tests: loading state, table with score+status, empty state, error state.

### File: `frontend/src/pages/CompanyDetail.test.tsx` (new)
- 4 tests: mismatch markers rendered, partial report no crash, mark-reviewed updates
  status, mark-reviewed error shown.

### File: `docs/implementation-notes.md` (appended)
- P1-T10 entry with key decisions and Phase 1 exit criteria confirmation.

### File: `docs/BUILD_PLAN.md` (updated)
- P1-T10 → Complete; Phase 1 exit criteria Met; Current ticket → P2-T1.

---

## Acceptance Criteria Mapping

- **Criterion:** Dashboard list — company name, analysis date, risk score, review status
  **Implementation:** `Dashboard.tsx` renders all four columns from `GET /reports/` list items.
  **Files:** `pages/Dashboard.tsx`, `api/client.ts`, `backend/app/api/reports.py`

- **Criterion:** Company detail — submitted-vs-discovered with match/mismatch indicators
  **Implementation:** `RegistrationDiff.tsx` renders a table per `mismatches[]` with
  color-coded badges (match=green/checkmark, mismatch=red/x, unverified=amber/?).
  `data-status` attribute on each row enables programmatic assertion.
  **Files:** `components/RegistrationDiff.tsx`, `pages/CompanyDetail.tsx`

- **Criterion:** Mark reviewed — action on detail view calls backend, writes audited review
  **Implementation:** "Mark Reviewed" button calls `POST /reviews/{run_id}` with
  `Authorization: Bearer <token>`. Backend writes `Review` row + `audit_event`.
  On success, the button is replaced by a confirmation banner.
  **Files:** `pages/CompanyDetail.tsx`, `api/client.ts`, `backend/app/api/reviews.py` (existing)

- **Criterion:** Operator sign-in (audited)
  **Implementation:** `AuthProvider` renders `SignInForm` when no token; calls
  `POST /auth/sign-in`; stores token in React state. Backend records audit event on
  successful sign-in (existing P1-T9 behavior).
  **Files:** `auth/AuthContext.tsx`, `api/client.ts`

- **Criterion:** Partial/in-progress reports render without breaking
  **Implementation:** `RegistrationDiff` checks `sectionStatus === "pending"` and shows
  a notice. `CompanyDetail` checks `section_statuses.scores === "pending"` and shows
  a pending notice. Tested explicitly in `CompanyDetail.test.tsx`.
  **Files:** `components/RegistrationDiff.tsx`, `pages/CompanyDetail.tsx`

---

## Build Plan Mapping

- Ticket: P1-T10 — Operator app: list + detail + mark reviewed
- Status: Complete
- What was completed: backend list endpoint (GET /reports/), typed API client,
  auth (sign-in form + session), dashboard list, company detail view with
  registration diff + risk score, mark-reviewed action. All tests green.
- Remaining work: None for P1-T10.

---

## Validation

### Frontend
- `npm run lint` — PASSED (0 errors, 0 warnings)
- `npm run test` — PASSED (10 tests: App x2, Dashboard x4, CompanyDetail x4)

### Backend
- `.venv/bin/ruff check .` — PASSED (All checks passed!)
- `.venv/bin/pytest -q` — PASSED (172 tests; 168 pre-existing + 4 new list-endpoint tests)

### Manual verification path
1. `cd backend && uvicorn app.main:app --reload`
2. Create an operator account (via psql or a seed script) with a hashed password.
3. `cd frontend && npm run dev`
4. Open http://localhost:5173 — sign-in form appears.
5. Sign in → dashboard renders (empty if no submissions yet).
6. Submit a registration via `POST /submissions` — pipeline runs.
7. Refresh dashboard → report row appears with score + "Pending Review".
8. Click row → detail view shows submitted-vs-discovered diff.
9. Click "Mark Reviewed" → banner confirms; re-opening detail shows reviewed state.

### Phase 1 exit criteria
All criteria are met end-to-end (backend tests verify stored report path;
frontend tests verify view → diff → mark-reviewed with mocked fetch):
- A submitted registration produces a stored, viewable report. (P1-T1 through T8)
- An operator can sign in (audited). (P1-T9 + AuthContext.tsx)
- Operator can see submitted-vs-discovered fields. (RegistrationDiff.tsx)
- Operator can mark it reviewed (audited). (CompanyDetail.tsx + reviews.py)

---

## Open Issues

- **N+1 query in `GET /reports/`** — resolves company name via individual
  `db.get()` calls per report row. Acceptable at MVP scale; batching is P3.
- **Token is in-memory only** — re-sign-in required on page refresh. Low priority
  for MVP; could be sessionStorage in a follow-up.
- **No pagination on dashboard** — `GET /reports/` returns all rows. Fine for MVP;
  add limit/offset when queue grows (P2-T10 adds filters/search).
- **Inline styles throughout** — no CSS framework; works but scales poorly.
  P2-T8 (detail view completeness) is a natural point to add Tailwind.
