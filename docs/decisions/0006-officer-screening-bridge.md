---
id: 0006
title: Screen a company's officers and owners through individual screening
anchor: ADR-0006
status: accepted
date: 2026-09-25
supersedes:
superseded-by:
---

Resolves the follow-up that PRD-IDV §[17] C9 left open ("automatic
cross-offering screening and feeding screening results into KYB risk scores
come later and need their own decision"). Backlog tickets 0078–0086.

## [1] Context

The original brief is about shell companies evading sanctions checks. Business
verification screens only the company name. A company fronted by a sanctioned
director or owner passes. Individual screening can already resolve a person
against the lists, and `ScreeningSubject.kyb_entity_id` exists for exactly this
link, but nothing feeds it.

The two offerings are deliberately separate packages (PRD §[14], build plan
"Package boundary"): KYB code never imports `app.screening`, and screening never
imports KYB domain modules. `tests/screening/test_models.py` enforces both.

## [2] Decision

- **Automatic screening.** Every KYB run collects the company's officers and
  owners (`collect_people`) and screens each one (`screen_people`) through the
  existing screening pipeline, as a `kyb_officer` run on a subject linked by
  `kyb_entity_id`.
- **Sources (v1).** People the submitter declares on the submission, plus
  registry officers/owners through a `RegistryPeopleProvider`. Live registry
  providers (OpenCorporates officers, UK Companies House officers + PSC) need
  credentials this build doesn't have, so they're deferred (ticket 0086); the
  production default is unconfigured and reports a `registry_people` coverage
  gap. No LLM extraction from web pages.
- **Scoring.** An officer/owner MATCH is a critical signal that forces
  `escalate`. REVIEW is an elevated, non-critical signal. CLEAR gives no trust
  credit, so a person can never help a company toward pre-clear.
- **A bridge package, not a broken boundary.** All coupling lives in
  `app/officer_screening/`, the only package allowed to import both offerings.
  KYB reaches it only by registering its stages in `default_stages`; screening
  never imports it (`tests/officer_screening/test_boundary.py`). Both original
  guards stay unchanged.
- **Names stay in screening.** A person's name and attributes are stored only in
  the subject's encrypted PII (ADR-0004). KYB tables (`company_person`,
  evidence) hold the relationship, role, sources, disposition and links, so
  crypto-shredding a subject leaves nothing readable on the KYB side. Declared
  people on the submission are submitted PII under ADR-0002 retention.
- **Queue.** `kyb_officer` screenings appear in the Individuals queue under
  their own trigger and need a human disposition like any other.

## [3] Consequences

- Running KYB now loads the screening code (through the bridge) and needs
  `ENTITYIQ_SCREENING_MASTER_KEY` to screen people. Without it, `screen_people`
  is unavailable and the run continues.
- A KYB stage timeout rolls back every screening in that run (the stage's
  session is isolated), so a run never ends with half its people screened.
- Ongoing monitoring already re-screens these subjects when a list changes. A
  later MATCH does not yet re-score the company; that needs a follow-up.
