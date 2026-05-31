# Implementation

## Scope Implemented
- Requested scope: Detail view completeness — DNS & domain panel, registry info,
  contact info with source attribution, and a risk-assessment panel (flags +
  operator notes) on the operator Company Detail view.
- Related phase: Phase 2 — Deepen the Tracks (Track: Operator workbench)
- Related ticket(s): **P2-T8 — Detail view completeness**

## Approach
- High-level strategy: **Frontend-only.** `GET /reports/{run_id}` already returns
  everything the panels need — Tier-2 domain evidence, Tier-1 registry evidence,
  Tier-3 web contact evidence (each with `source`/`field`/`attribution`), the
  four-layer scores, and `contributing_signals` (risk flags / trust signals).
  The panels are pure presentational components that read from the existing
  `ReportResponse`; no backend, schema, or pipeline changes were required.
- Key decisions:
  - Added four content components in a single shared file
    (`components/DetailPanels.tsx`) with shared `FieldRow` / pending / empty
    primitives, rather than four near-duplicate files. Matches the existing
    "one focused component module" pattern (`RegistrationDiff.tsx`) while staying DRY.
  - Each panel handles the three partial-result states already used elsewhere:
    `pending` (section still computing), no-relevant-evidence (graceful "not
    available" notice), and populated. This preserves the codebase's
    partial-result-safe rendering contract.
  - "Operator notes" (part of the P2-T8 risk panel requirement) is delivered by
    adding an optional notes textarea to the **existing** Mark-Reviewed action,
    which already accepts `notes` via `MarkReviewedRequest`. The broader operator
    actions (correct data, re-run, export, dashboard filters) remain P2-T10.
  - "DNS risk score" (PRD § DNS & Domain Intelligence) is surfaced as the
    `infrastructure_score` line plus inline `recently_registered` / `no_mx`
    flag badges, since there is no separate per-DNS score in the model.
- Assumptions:
  - Evidence `field` / `source` names are stable (verified against the
    domain / opencorporates / web adapters): e.g. `domain_registrar`,
    `registration_status`, `web_contacts_email`, etc.
  - Contact list payloads (full email/phone lists) live in `raw_payload` and are
    not exposed by the report API; the panel shows the primary discovered value
    plus its `attribution.source_url`, which satisfies "source attribution".

---

## Implementation Plan
1. `api/client.ts` — give `contributing_signals` a real type (`ContributingSignal`).
2. `components/DetailPanels.tsx` (new) — `DomainPanel`, `RegistryPanel`,
   `ContactPanel`, `RiskAssessmentPanel` + shared helpers/styles.
3. `pages/CompanyDetail.tsx` — render the four panels in new sections; add an
   optional review-notes textarea wired into the existing `markReviewed` call.
4. `pages/CompanyDetail.test.tsx` — extend the fixture with evidence + signals +
   sources; assert panels render (populated and partial/pending states).

Files to modify/create:
- Create: `frontend/src/components/DetailPanels.tsx`
- Modify: `frontend/src/api/client.ts`, `frontend/src/pages/CompanyDetail.tsx`,
  `frontend/src/pages/CompanyDetail.test.tsx`

---

## Code Changes

### File: frontend/src/components/DetailPanels.tsx  (new)
- Change summary: Four read-only Company Detail panels built from existing report
  data, plus shared `FieldRow` / `Pending` / `NotAvailable` primitives and inline
  styles consistent with the app.
  - `DomainPanel` — domain age (+ recently-registered flag), registrar, created /
    expires, MX (+ no-mail flag), SPF, DKIM, SSL issuer/subject/validity,
    infrastructure score.
  - `RegistryPanel` — registered name, registration status, jurisdiction,
    registration number, legal address (Tier-1 `opencorporates`).
  - `ContactPanel` — brand, email, phone, address, each with source attribution
    (`attribution.source_url`).
  - `RiskAssessmentPanel` — four layer-score cells, triage tier, evidence summary
    (source + evidence counts), elevated-risk flags, and trust signals.

### File: frontend/src/api/client.ts
- Change summary: Added `ContributingSignal` interface and typed
  `ScoresData.contributing_signals` as `ContributingSignal[]` (was
  `Record<string, unknown>[]`).

### File: frontend/src/pages/CompanyDetail.tsx
- Change summary: Imported and rendered the four panels in new sections (DNS &
  Domain Intelligence, Registry Information, Contact Information, Risk Assessment)
  between the Registration Data diff and the Operator Action. Added an optional
  review-notes textarea to the Mark-Reviewed action; `notes` is sent to
  `markReviewed` only when non-empty.

### File: frontend/src/pages/CompanyDetail.test.tsx
- Change summary: Extended `COMPLETE_REPORT` with domain/registry/web evidence,
  contributing signals (one elevated, one trust), and source summaries. Added two
  tests: panels render from a complete report; panels show pending notices for a
  partial report.

---

## Acceptance Criteria Mapping
PRD § Company Detail View:

- Criterion: **DNS & Domain Intelligence** (domain age, registrar, MX/SPF/DKIM,
  SSL metadata, DNS risk score)
  - Implementation: `DomainPanel` renders all fields from Tier-2 `domain` evidence;
    infrastructure score + recently-registered / no-MX flags cover "DNS risk score".
  - File(s): `components/DetailPanels.tsx`, `pages/CompanyDetail.tsx`

- Criterion: **Registry Information** (registration status, jurisdiction,
  registration identifiers, legal address)
  - Implementation: `RegistryPanel` from Tier-1 `opencorporates` evidence.
  - File(s): `components/DetailPanels.tsx`, `pages/CompanyDetail.tsx`

- Criterion: **Contact Information** (names, phones, emails, addresses, source
  attribution)
  - Implementation: `ContactPanel` from Tier-3 `web` evidence with
    `attribution.source_url` shown per value.
  - File(s): `components/DetailPanels.tsx`, `pages/CompanyDetail.tsx`

- Criterion: **Risk Assessment** (overall score, evidence summary, risk flags,
  operator notes)
  - Implementation: `RiskAssessmentPanel` (layer scores, triage tier, evidence
    summary, elevated + trust signals) + review-notes textarea on the existing
    Mark-Reviewed action.
  - File(s): `components/DetailPanels.tsx`, `pages/CompanyDetail.tsx`

---

## Build Plan Mapping
- Ticket: **P2-T8 — Detail view completeness**
  - Status: **Complete**
  - What was completed: All four detail panels (DNS/domain, registry, contact with
    attribution, risk assessment with flags) plus operator-notes capture, driven by
    the existing report API. Partial-result-safe. Lint + tests green.
  - Remaining work: None for this ticket. The map/address-confidence visualization
    is **P2-T9** (blocked on Open Decision #4); full operator actions (correct data,
    re-run, export, dashboard filters) are **P2-T10**.

---

## Validation
- How the feature was tested: Vitest component tests render `CompanyDetail` with a
  full report fixture (domain/registry/web evidence + signals + sources) and assert
  each panel's content (registrar, recently-registered flag, registration status,
  contact email + source attribution, risk flag/trust lists, evidence summary);
  a second test asserts pending notices for a partial report.
- Lint/test results:
  - `npm run lint` → clean (0 errors/warnings).
  - `npm test` → **12 passed (3 files)**.
  - `tsc --noEmit`: production files added **zero** new errors; the only
    `CompanyDetail.tsx` errors are the pre-existing `auth is possibly null` pattern
    (present at baseline, lines 59/75/85 → shifted to 66/82/95). The repo has a
    pre-existing strict-null backlog validated via lint + vitest.
- Manual verification steps: `cd frontend && npm run dev`, open a completed run in
  the dashboard → the detail view now shows DNS & Domain Intelligence, Registry
  Information, Contact Information, and Risk Assessment panels, plus a notes field
  on Mark Reviewed.
- Visible user outcome: Operators get the full PRD Company Detail View — domain/DNS
  signals, authoritative registry data, attributed public contacts, and an
  explainable risk panel with flags — instead of just score + registration diff.

---

## Open Issues
- Known limitations:
  - Contact panel shows the primary discovered value per type; full extracted lists
    live in `raw_payload` and are not exposed by the report API (out of scope).
  - "DNS risk score" is represented by the infrastructure layer score + flags;
    there is no standalone per-DNS subscore in the data model.
- Unresolved edge cases: None observed; panels degrade gracefully to "not
  available" / pending notices.
- Blockers: None for P2-T8. (Pre-existing project blockers unchanged: Open
  Decision #5 Tier-1 licensing; IPINFO_TOKEN production plan.)

---

## BUILD_PLAN Update
- Current phase: Phase 2 — Deepen the Tracks
- Current ticket: P2-T8 — Complete
- Updated ticket status: P2-T8 → Complete
- Blockers: unchanged (#5 licensing, IPINFO_TOKEN; #4 blocks P2-T9, #7 blocks P3-T3)
- Recommended next ticket: **P2-T10 — Full operator actions + dashboard filters**
  (P2-T9 is blocked on Open Decision #4 — map provider).
