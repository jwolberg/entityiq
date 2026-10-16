# Feature Spec

> **Superseded in detail by the plan.** `docs/plans/2026-10-16-001-feat-public-records-sources-plan.md` governs where this spec differs. In particular: an active federal exclusion **is** critical (KTD11); an open bankruptcy floors the tier at review (KTD14); there is no UEI matching in v1 (KTD10); there is no litigation-as-defendant signal (R11); and `triage.py`/`engine.py` do change. This spec is kept as the original request record.


## Feature Request
- Original request: Thomson Reuters CLEAR public records integration: liens, judgments, bankruptcies, criminal records, lawsuits, business/people screening & monitoring, sanctions/PEP/OFAC, adverse media, court document retrieval — scoped against EntityIQ's existing pipeline
- **Revised direction (2026-10-16):** build our own capability on **official, free public sources** instead of buying CLEAR. People-finder scraping was considered and rejected: those sites disclaim FCRA-type use, sit behind bot protection, and produce non-citable evidence.

## Problem Statement
- **What it solves:** The KYB pipeline confirms that a business *exists* and *is who it says it is* (registry, FEIN, domain, web, LinkedIn, OFAC, officer screening). It has no view of the business's **derogatory public record**. A registered, OFAC-clean company can `pre_clear` even if it is in bankruptcy, is being sued in federal court, or has been debarred by the US government.
- **Who:** Compliance operators reviewing the queue; leads accountable for the call.
- **Why it matters:** Bankruptcy, litigation, and debarment are standard KYB due-diligence checks. Today operators run them by hand outside EntityIQ, or skip them, and the search is not in the audit trail. Both sources below are deterministic and citable (docket number, court, SAM record), matching the standing preference for registry-style evidence.

## Target User and Workflow
- **Primary user:** Compliance operator (`operator`); lead (`lead`) on escalations.
- **Current workflow:** Read report → search PACER/CourtListener/SAM.gov by hand if suspicious → decide with no record of that search.
- **Desired workflow:** The pipeline searches federal court dockets and the SAM.gov exclusions list for the resolved business. The report shows a **Public records** panel with each matched docket or exclusion (type, status, dates, court/agency, docket number or SAM record, link to the source). Material findings raise entity-layer risk with a plain-language explanation that cites the record.

## Success Condition
- Using recorded fixtures, a fictional demo company with an open bankruptcy docket and an active SAM exclusion moves out of `pre_clear`, and its report cites the docket and the exclusion. A clean company's tier doesn't change.
- With no CourtListener token configured and no SAM extract loaded, every run behaves exactly as today: the sources show unavailable, with no evidence, signals, or score change.

## Scope
**In scope (v1: business entity only, two free official sources)**
1. **Federal court dockets via CourtListener** (Free Law Project REST API v4, free token)
   - Bankruptcy dockets where the business is the debtor.
   - Federal civil dockets where the business is a party. v1 counts **open** cases only. Role (defendant vs. plaintiff) is taken from party data when the API returns it; otherwise the case is shown but not scored.
2. **SAM.gov exclusions** (GSA): debarment, suspension, and other federal exclusions.
   - Ingested from the **daily public exclusions extract** (a bulk file, no per-search quota) into a local snapshot, the same way OFAC SDN is ingested (`app/lists/ofac.py`). We don't call the per-entity API: a non-federal personal key is limited to **10 requests/day**.
   - Matched by UEI when we have one, otherwise by normalized name plus state.
- One new adapter + stage `public_records`, with an injectable client per source. A source with no credentials or no snapshot reports `unavailable`.
- Entity-layer signals, a report panel, and demo seed data from recorded fixtures.

**Out of scope (explicit, with reason)**
- **People-finder scraping and people data APIs (e.g. EnformionGO).** These raise FCRA/ToS questions and their evidence isn't citable. Rejected 2026-10-16.
- **Criminal records / people screening.** Officers are already screened against official lists (ADR-0006). No new people sources.
- **State-court judgments, tax liens, and UCC filings.** There is no free consolidated source; the records are split across 50 states and thousands of counties. This is the main coverage gap versus CLEAR, documented as such. The follow-up is a paid aggregator or one-state-at-a-time adapters.
- **Buying documents from PACER** (CourtListener's RECAP fetch uses our PACER account and is billed). v1 uses docket metadata already on CourtListener and links to it.
- **CSL, regulatory enforcement, and identity sources:** planned as Phases 2–4 below, not part of v1.
- **Free-key sources that aren't free for business use:** OpenSanctions (non-commercial use only; businesses need a license, which makes it a paid candidate for IS3-T1 PEP) and OpenCorporates (free tier only for open-data projects, journalists, NGOs, and academics; already Open Decision #5).
- **PEP / adverse media** (stays icebox IS3-T1), **monitoring/re-screening**, and **document retrieval**.

**Later phases (planned; each is its own ticket and its own adapter/stage, following the one-source-per-adapter pattern so one outage doesn't hide the others)**

Every phase reuses this spec's rules: unconfigured means unavailable, fictional fixtures only, no network in tests, absence is never a trust signal, weak matches never score, and nothing new becomes critical without a separate triage decision. Every phase **starts by verifying the endpoint, auth format, rate limits, and commercial-use terms** against official docs. Items marked † were found only in third-party documentation as of 2026-10-16.

- **Phase 2: trade.gov Consolidated Screening List (CSL)**: 11 US export-control and sanctions lists (Commerce, State, Treasury). Public domain, updated daily.
  - Access: the downloadable CSV/JSON (no key) ingested as a list snapshot via `app/lists/`. The search API (free subscription key; endpoint/header format †) is the fallback.
  - Where: **extends `sanctions_screening`**, not `public_records`. A CSL hit is a sanctions/export hit, so it uses the existing `sanctions_hit` path (already critical). The evidence records which list matched.
- **Phase 3: Regulatory enforcement** (one adapter each, all low weight because routine findings are common for legitimate companies):
  - **EPA ECHO**: environmental violations and formal enforcement. No key; two-step query (QID) pagination. Signal only on formal enforcement actions within 5 years.
  - **DOL Wage & Hour enforcement** †: concluded compliance actions since FY2005. Free key, sent as a query parameter †. Signal on repeat/willful findings or back wages above a threshold (set in the phase ticket).
  - **CFPB consumer complaints** †: run only when the submission's industry is financial services. No key †. Note: institutions under $10B in assets aren't published. Shows evidence (complaint volume, untimely responses), with weight ≤0.2.
- **Phase 4: Identity and ownership corroboration** (feeds entity resolution and ownership, not derogatory scoring):
  - **GLEIF LEI API** †: no key; license reported as CC0 †. Gives LEI, legal name, registered address, and direct/ultimate parent. It feeds `resolve_entity_candidates` and `pipeline/ownership.py` as a Tier-1 corroborating source, and can provide a trust signal (a registry-grade identity match, unlike "no derogatory records").
  - **SEC EDGAR**: no key; requires a descriptive User-Agent with a contact email and a hard limit of 10 requests/second (exceeding it gets the IP blocked), so the client must throttle. Public companies only: CIK, filer status, state of incorporation. Runs only when the name resolves to a CIK. Trust/identity corroboration, plus "filer delinquent/revoked" as an elevated signal if the data supports it.

## Constraints
**Technical**
- Adapter satisfies `SourceAdapter`, never raises, and is wrapped in `RetryingAdapter`. The CourtListener client honors 429 (`rate_limited`) and has a per-stage time budget.
- **No live network in tests.** CourtListener tests use recorded JSON fixtures of *fictional* parties, and SAM tests use a small synthetic extract file. No real company's records are committed.
- Credentials only from env: `COURTLISTENER_API_TOKEN` (never logged). The SAM extract location comes from config, and the ingest command downloads it at runtime, never committing it.
- New stage added to `default_stages()`, `test_pipeline_e2e.py`, **and** `demo_data.py` (the guard test enforces order). Position **3e**, after `screen_people`, before `analyze_domain`.
- The SAM snapshot reuses the `app/lists/` snapshot/ingest pattern. If that needs a new table, it is an additive Alembic migration that works on SQLite and Postgres. Prefer reusing existing snapshot storage.
- No new dependencies (`httpx`/`requests` and `csv`/`zipfile` are already available; verify before implementation).
- **To verify in the RED-test ticket** (the docs I fetched didn't confirm these): CourtListener's party-name query operator, its bankruptcy-court filter syntax, the fields returned for date terminated and chapter, its rate limits, and its commercial-use terms. If party data isn't available on search results, the scope falls back to bankruptcy only.

**Product**
- Explainable, never auto-approves. Every signal names the docket or exclusion it rests on, with a source link.
- **Absence is not a trust signal.** RECAP coverage of federal dockets is partial, and new shells are clean by definition. "No records" is shown but scores zero.
- **No new critical (forced-escalate) signals.** Changing what forces escalation is a triage policy decision. It is flagged as a follow-up for active SAM exclusions.
- Initial signals (entity layer, elevated):
  - `public_records_open_bankruptcy`: open bankruptcy docket filed within 36 months, strong match. Weight 0.8.
  - `public_records_active_federal_exclusion`: active SAM exclusion (no termination date or a future one), strong match. Weight 0.9.
  - `public_records_federal_litigation_as_defendant`: ≥3 open federal civil cases with the business as defendant. Weight 0.3.

**Existing patterns to reuse**
- `app/lists/ofac.py` + `ingest.py`/`snapshot.py` for the SAM extract.
- `tax_id.py` for the injectable client, unconfigured default, and `names_match`/`core_name`.
- `_tax_id_signals` in `signals.py` as the model for `_public_records_signals`. Exclude `public_records` from the generic Tier-1 trust count (`signals.py:95`).

## Edge Cases
- **Name collisions (common in dockets):** a strong match requires `names_match` on the party/debtor name **and** no state conflict (bankruptcy district or SAM address state vs. registry/submitted state). Otherwise the record is stored as `match=weak`, shown under "Possible matches", and **never scored**.
- **SAM UEI match** beats a name match. A UEI conflict, where the same name has a different UEI than the one we know, makes the record weak.
- **No resolved entity:** search on the submitted name; all results are weak.
- **Non-US submission:** `public_records_status=not_applicable`, and no clients are called.
- **Closed, dismissed, or discharged bankruptcies, terminated cases, and terminated exclusions:** shown, not scored.
- **Stale SAM snapshot** (older than 7 days): still used, and the panel shows the snapshot date with a stale marker. No snapshot means `unavailable`.
- **CourtListener timeout / 429 / 5xx:** `AdapterFailure`, so this source is unavailable while the SAM side can still return. The stage emits evidence for whichever source succeeded.
- **Large result sets:** cap at 50 dockets per category, most recent first, with the true count in a `public_records_summary` row. Search counts over 2,000 are approximate per CourtListener docs and are labeled "approx."
- **Re-run:** new evidence for the new run; prior evidence untouched (append-only).

## Acceptance Criteria
1. With no `COURTLISTENER_API_TOKEN` and no SAM snapshot, a full run produces no record evidence, both sources report `unavailable`, and score and tier match a run without the stage.
2. A fixture with an open Chapter 7/11 docket (≤36 months, strong match) fires `public_records_open_bankruptcy`, and the explanation cites the court, docket number, and filed date.
3. A synthetic SAM extract with an active exclusion matching the submission's UEI fires `public_records_active_federal_exclusion`, citing the excluding agency and activation date. The same record with a termination date in the past fires nothing.
4. Three open federal civil cases as defendant fire `public_records_federal_litigation_as_defendant`. Two cases, three as plaintiff, or cases with unknown role fire nothing.
5. Weak matches (name fails, state conflicts, UEI conflicts) appear under possible matches and fire nothing.
6. A clean result writes a `no_records` row per source and **no trust signal**. The score equals the unconfigured score.
7. None of the three signal names is in `CRITICAL_ESCALATION_SIGNALS` (asserted).
8. CourtListener `TimeoutError` / 429 / other error → `timeout` / `rate_limited` / `unavailable`. A SAM hit in the same run is still recorded and scored, and the run completes.
9. Non-US submission → `not_applicable`, and neither client is called.
10. A snapshot older than 7 days is used and marked stale in the evidence.
11. The SAM ingest command parses the synthetic extract fixture into the snapshot. Re-running it on the same file is idempotent.
12. Stage-order guard test passes with `public_records` at 3e.
13. Demo seed: the designated fictional company rises out of `pre_clear` and its panel shows the docket and exclusion. Every other demo company keeps its tier.
14. The detail page renders the Public records panel in each state (strong matches, possible matches, no records, unavailable, not applicable, stale snapshot). A vitest covers each.

## Implementation Outline
1. **Verify the CourtListener API** (party/court query syntax, fields, rate limits, terms) with a single manual call using a real token. Record a sanitized, fictionalized fixture shape. Log findings in implementation-notes.
2. **RED tests:** adapter (AC 1, 4, 5, 8, 9, 10), signals (AC 2, 3, 6, 7), ingest (AC 11), order guard (AC 12), frontend panel (AC 14).
3. `backend/app/lists/sam.py`: extract download + parse + snapshot (mirrors `ofac.py`), plus an ingest CLI entry.
4. `backend/app/adapters/public_records.py`: `CourtListenerClient` (injectable), SAM snapshot lookup, match classification, Evidence rows (`source="public_records"`, tier 1):
   - `public_records_status` per source: searched | no_records | not_applicable | stale
   - `public_records_summary`
   - one `public_record` row per docket or exclusion, with a JSON value: kind, status, filed/activated date, terminated date, court/agency, docket number / SAM id, party name, role, match, source URL
   - `raw` holds the provider payload
5. `signals.py`: `_public_records_signals()` + the trust-count exclusion.
6. Wire into orchestrator, the e2e test, and `demo_data.py` (fixtures for one company).
7. Frontend `PublicRecordsPanel`.
8. Docs: implementation-notes (direction change, coverage gap, SAM rate-limit finding), ARCHITECTURE sources table, RUNBOOK (token + extract ingest), BUILD_PLAN tickets, and tickets for Phases 2–4 plus the remaining follow-ups (state-court/lien source, SAM exclusion critical-escalation policy).

## File Impact Guess
*(estimate only)*
- **New:** `backend/app/lists/sam.py`, `backend/app/adapters/public_records.py`, `backend/tests/adapters/test_public_records.py`, `backend/tests/lists/test_sam.py`, `backend/tests/fixtures/public_records/*` (fictional), `frontend/src/components/PublicRecordsPanel.tsx` + test
- **Changed:** `backend/app/pipeline/orchestrator.py`, `backend/app/scoring/signals.py`, `backend/app/demo_data.py`, `backend/tests/pipeline/test_pipeline_e2e.py`, `frontend/src/components/DetailPanels.tsx`, `frontend/src/api/client.ts`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`, `docs/BUILD_PLAN.md`, `docs/implementation-notes.md`
- **Possibly:** one additive Alembic migration if SAM snapshot rows can't reuse existing list-snapshot storage.
- **Not changed:** `triage.py`, auth, audit.

## Validation Plan
- Backend gate: `cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q`
- Frontend gate: `cd frontend && npm run lint && npm run test && npm run build`
- Regression: record demo tiers/scores before and after. Only the designated company changes (AC 13), and the unconfigured run is identical (AC 1).
- Manual (one-off, not in CI): with a real token, run the stage against two or three known public bankruptcy debtors to confirm match quality. Record results in implementation-notes without committing real-company data.
- Code-review agent to the merge bar.
