# EntityIQ

**Business verification and individual sanctions screening, with explainable
decisions a compliance team can defend.**

EntityIQ has two offerings on one platform. They share the same operator
workbench, auth, audit log and database.

- **EntityIQ Business Verification.** When a company registers itself for an
  enterprise account, someone has to decide whether it's real, whether it's
  who it says it is, and whether it's safe to approve. EntityIQ gathers
  evidence from:
  - business registries;
  - sanctions lists;
  - domain and email infrastructure;
  - network data;
  - the company's own website.

  It checks what was submitted against what it found and produces a
  **deterministic, explainable risk score** that sorts the review queue.
- **EntityIQ Individual Screening.** It answers one question: *is the person
  in front of us the same person as this sanctions-list record?* It screens
  people against the official OFAC, UN, EU and UK lists. It scores every
  candidate match with named, weighted terms, each of which cites its source.
  Every decision is recorded with frozen inputs, so it can be **replayed to
  the same verdict** after the lists have changed.

Neither offering approves anyone. A human makes every decision, and every
action is audited.

![Verification queue](docs/img/dashboard.png)

## Checks, in order

Each check below links to the code that performs it. Each entry names the
class or function rather than a line number, so you can search for it.

### Business verification

The stages run in this order, set in `default_stages()` in
[`pipeline/orchestrator.py`](backend/app/pipeline/orchestrator.py). If a source
fails, that stage is marked "unavailable" and the run continues.

| # | Check | What it checks | Code |
|---|---|---|---|
| 1 | Normalize input | Canonicalizes the submitted name, country, address, domain and tax ID. Produces no verdict. | [`pipeline/normalize.py`](backend/app/pipeline/normalize.py) `NormalizeInputStage` |
| 2 | Resolve entity | Ranks the candidate companies the submission could be, and flags conflicting identities. Runs offline. | [`pipeline/resolve.py`](backend/app/pipeline/resolve.py) `ResolveEntityCandidatesStage` |
| 3 | Business registry | OpenCorporates: is the name confirmed, is there a registration number, is the company active or inactive? | [`adapters/opencorporates.py`](backend/app/adapters/opencorporates.py) `QueryRegistriesStage` |
| 4 | Tax ID (FEIN) | Does the FEIN exist, is its entity active, and is it registered to the submitted name? | [`adapters/tax_id.py`](backend/app/adapters/tax_id.py) `VerifyTaxIdStage` |
| 5 | Company sanctions | The company name against OFAC SDN: exact match after removing legal suffixes such as Inc and LLC. | [`adapters/sanctions.py`](backend/app/adapters/sanctions.py) `SanctionsScreeningStage` |
| 6 | Collect officers & owners | Merges the people declared on the submission with the registry's officers and owners, removing duplicates. | [`officer_screening/people.py`](backend/app/officer_screening/people.py) `CollectPeopleStage` |
| 7 | Screen officers & owners | Runs each person through individual screening (below). | [`officer_screening/screen.py`](backend/app/officer_screening/screen.py) `ScreenPeopleStage` |
| 8 | Domain & email | Domain age from WHOIS (flagged when under 180 days), DNS, MX, SPF, DKIM, and the SSL certificate. | [`adapters/domain.py`](backend/app/adapters/domain.py) `AnalyzeDomainStage` |
| 9 | Network / IP | IPinfo for the submitter's IP: does its country match the submission, and is it a hosting, VPN or proxy address? Also the ASN (the network operator). | [`adapters/ipinfo.py`](backend/app/adapters/ipinfo.py) `EnrichNetworkIPStage` |
| 10 | Public web | Site content, contact pages and press footprint. Extracts contacts, each attributed to its source. | [`adapters/web.py`](backend/app/adapters/web.py) `WebEvidenceStage` |
| 11 | LinkedIn | Is there a company page, and is it established, thin or new? Does its website match the domain, and is the requester associated with it? | [`adapters/linkedin.py`](backend/app/adapters/linkedin.py) `VerifyLinkedInStage` |
| 12 | Consistency | Submitted vs. discovered values: name, country vs. jurisdiction, billing vs. legal address, tax-ID status and registered name, LinkedIn website vs. domain. Each comes out as match, mismatch or unverified. | [`pipeline/consistency.py`](backend/app/pipeline/consistency.py) `ConsistencyChecksStage` |
| 13 | HQ geocode | Puts the HQ on a map with OpenStreetMap Nominatim and rates address confidence high, medium or low by how many sources agree. | [`adapters/geocode.py`](backend/app/adapters/geocode.py) `GeocodeHQStage` |
| 14 | Scoring & triage | Turns the evidence into signals with a layer, a direction and a weight. Scores four layers 0–100, combines them into an overall score, and assigns a tier: `pre_clear` (≤ 30), `review` (31–69) or `escalate` (≥ 70). Critical signals force `escalate`. | [`scoring/engine.py`](backend/app/scoring/engine.py) `ScoringStage`, `_triage_tier`, `CRITICAL_ESCALATION_SIGNALS` |
| 15 | Report | Assembles the queryable report the operator UI reads. | [`scoring/report.py`](backend/app/scoring/report.py) `StoreReportStage` |

The scoring step (14) also computes these signals without a stage of their
own. All are in [`scoring/signals.py`](backend/app/scoring/signals.py):

- **Free email domain.** The submitter used a webmail address. See `_append_intake_signals`.
- **Domain ownership proven.** Uses an optional DNS TXT, meta tag or email
  challenge. See [`pipeline/ownership.py`](backend/app/pipeline/ownership.py)
  and `_append_ownership_signals`.
- **Cross-submission reuse.** The submitter's ASN appears on other
  submissions in the last 30 days, not counting re-analyses or the same
  company resubmitting. Three or more force `escalate`. See
  `_cross_submission_reuse_signals`.
- **Officer screening results.** A MATCH forces `escalate`; a REVIEW adds an
  elevated signal. See `_officer_screening_signals`.

### Individual screening

The screening stages run in this order, set in `screening_stages()` in
[`screening/pipeline.py`](backend/app/screening/pipeline.py).

| # | Check | What it checks | Code |
|---|---|---|---|
| 1 | List coverage | Each required list (OFAC SDN, UN, EU, UK OFSI) is present and less than 7 days old. A missing or stale list blocks auto-CLEAR. | [`screening/stages.py`](backend/app/screening/stages.py) `list_coverage` |
| 2 | Block candidates | Finds candidates recall-first against every list. It tolerates transliteration, name order, particles, nicknames and initials. | [`screening/stages.py`](backend/app/screening/stages.py) `BlockCandidatesStage`, [`screening/blocking.py`](backend/app/screening/blocking.py) |
| 3 | Score candidates | Applies named, weighted terms for name, date of birth, ID number, nationality and place of birth, plus a common-name penalty. Conflicts appear as their own negative terms. | [`screening/scoring.py`](backend/app/screening/scoring.py) `TERM_CATALOG`, `DEFAULT_RULE`, `score_pair` |
| 4 | Dispose | Assigns MATCH (≥ 0.9), REVIEW or CLEAR (< 0.35). A name match with a DOB or ID conflict can fall no lower than REVIEW. CLEAR closes without a human only when every list answered. | [`screening/dispose.py`](backend/app/screening/dispose.py) `decide`, `DisposeStage` |
| 5 | Ongoing monitoring | When a list changes, re-screens existing subjects against only the added and changed records. | [`screening/monitor.py`](backend/app/screening/monitor.py) `rescreen_for_snapshot` |

## Quick start

```bash
./scripts/demo.sh
```

Needs Python 3.11+ and Node 20+. No Docker, Postgres, or Redis. On first run
the script sets everything up and loads the demo data:

- seven fictional companies that span every risk tier, with their officers and
  owners (one of whom is on the demo sanctions list);
- four fictional people that span every screening outcome (MATCH, REVIEW and
  auto-CLEAR).

It then serves:

- **UI:** http://localhost:5173. The password for every demo account is
  `entityiq-demo`. Sign in as:
  - `lead@demo.entityiq.dev`;
  - `operator@demo.entityiq.dev`;
  - `examiner@demo.entityiq.dev` (read-only).

  **Businesses** and **Individuals** are separate sections in the nav.
- **API docs:** http://localhost:8000/docs

The demo runs through the real pipelines with recorded source responses and a
fictional watchlist, so it's deterministic and works offline. Details are in
[docs/RUNBOOK.md](docs/RUNBOOK.md).

## What it does

### Business verification

| | |
|---|---|
| **Four-layer scoring** | Four layers are each scored 0–100 and combined into an overall score: entity legitimacy, infrastructure legitimacy, representation confidence, and fraud/staging risk. |
| **Triage, not verdicts** | Every run lands in `pre_clear`, `review`, or `escalate`. Critical signals (such as a sanctions match) force `escalate` whatever the score. Nothing is ever auto-approved. |
| **Explainable** | Every signal cites the evidence rows behind it, with source attribution. An unavailable source *lowers confidence*; it's never counted as "low risk". |
| **Officers & owners screened** | The officers and owners behind a company (declared on the submission, or found in the registry) each go through individual screening. An officer or owner who comes back as a MATCH forces `escalate`, even when the company itself looks clean; a REVIEW adds an elevated signal. The workbench draws them as an ownership graph around the company, linked to each person's screening. |
| **Identity corroboration** | Checks the tax ID (FEIN) and LinkedIn company page against the submission. Both run on stub providers until live vendors are chosen (see known gaps). |
| **Domain-ownership proof** | An optional challenge (DNS TXT, email or meta tag) the registrant can complete to raise confidence that they control the domain. |
| **Operator workbench** | Everything for reviewing one case: the submitted-vs-discovered diff, domain/DNS, registry, contacts, and an HQ map with address confidence. Operators can also flag risks, review with notes, correct and re-run, export JSON, and see a per-case activity timeline. |
| **Integration API** | Endpoints: `POST /submissions` (API key or operator token, idempotent), report read and export, and re-analysis. Leads provision API keys in the UI. The client IP is captured at the trusted edge, so a client can't spoof it via `X-Forwarded-For`. |

**A verification run.** Wingtip Freight Ltd (a fictional demo company) looks
clean, with an overall risk score of 10. Its declared majority owner, however,
is a MATCH on the (fictional) sanctions list, so the run is forced to
`escalate`. The risk assessment names the signal behind that and cites its
evidence. The Officers & Owners panel draws each person around the company,
with their screening result; clicking a person opens their screening.

<p>
  <img src="docs/img/run-escalate.png" width="49%" alt="Verification run: overall score 10, triage escalate" />
  <img src="docs/img/run-risk-assessment.png" width="49%" alt="Risk assessment: officer_sanctions_match forces escalation, with trust signals" />
</p>

![Officers & Owners: the owner is a MATCH, the registry director is CLEAR](docs/img/run-officers-owners.png)

<p>
  <img src="docs/img/detail-volga.png" width="49%" alt="Company detail — sanctions escalation" />
  <img src="docs/img/audit-log.png" width="49%" alt="Lead audit log" />
</p>

![HQ map with address confidence](docs/img/hq-map.png)

### Individual screening

| | |
|---|---|
| **Official lists, versioned** | Parses OFAC SDN, UN, EU and UK OFSI into person records with names, aliases, DOBs, nationalities, places of birth and ID documents. Each ingest is a snapshot identified by its content hash. |
| **Recall-first matching** | Candidates are found despite transliteration, reversed name order, particles and patronymics (al, bin, van, von), accents, nicknames and initials. A record that matches the whole name is never dropped, even for very common names. |
| **Named, cited scoring** | Terms include `name_exact_normalized`, `dob_full_match`, `id_number_match` and `dob_conflict`. Each has a declared weight and cites its evidence. Conflicts appear as their own terms and are never averaged away. |
| **CLEAR / REVIEW / MATCH** | REVIEW is the "not sure" band. A run auto-closes as CLEAR only when every candidate is below the clear threshold and every required list is present and fresh. A missing list is never counted as "no risk". |
| **Replayable decisions** | Each decision freezes its inputs, list snapshots, rule version, terms and thresholds. `POST /screenings/{id}/replay` recomputes the verdict with no list or network access. |
| **"Why this decision?" panel** | A pull-out sidebar walks through every decision a run made: lists checked, candidates found, how each scored and why it got its band, the final disposition, and human review. Each piece of evidence is a citation naming the list, entry, field and list date. It expands to the snapshot hash and the published list file. Backed by `GET /screenings/{id}/explanation`. |
| **Ongoing monitoring** | A new list snapshot re-screens affected subjects as new runs. Past decisions are never edited. |
| **Privacy by design** | Each subject's data is encrypted with its own key. Once the 5-year AML retention period ends, the key is destroyed (crypto-shredding), which leaves the append-only records intact but unreadable. |
| **Examiner role** | A read-only role for regulators. It can view every screening and the audit log and replay decisions, but can't write. |

**A screening result with "Why this decision?" open** (`?why=1`). The subject
matches the list entry on name, full date of birth and nationality. The panel
walks through the lists checked, the candidates found, each scoring term with
its citation, the final MATCH, and the human review still to come.

![Screening result for a MATCH with the "Why this decision?" panel open](docs/img/screening-why.png)

## How it works

**Business verification:**

```
POST /submissions ──► FastAPI ──► Submission + VerificationRun (pending)
                                          │  Celery (Redis, or inline in dev)
                                          ▼
  normalize → resolve → registries → tax ID → sanctions → officers/owners
            → screen each person → domain → IP → web → LinkedIn
            → consistency → geocode HQ → scoring → report
                                          │
      Operator UI (React) ◄── GET /reports ┘      every stage isolated:
                                                   a failed source is marked
                                                   "unavailable", the run continues
```

**Individual screening:**

```
POST /screenings ──► FastAPI ──► encrypted subject + ScreeningRun
                                          │  dedicated "screening" Celery queue,
                                          ▼  run budget in seconds
     block candidates → score terms → dispose (CLEAR / REVIEW / MATCH)
                                          │
                                          ▼
     append-only decision: frozen inputs · snapshots · rule version · terms
                                          │
     Individuals UI ◄── GET /screenings/{id} (+ /explanation, /replay)
```

- **Sources:**
  - OpenCorporates (registry);
  - OFAC SDN, UN, EU and UK OFSI (sanctions);
  - WHOIS/DNS/SSL (domain, MX, SPF, DKIM);
  - IPinfo (geo, ASN, VPN/hosting);
  - the company website (branding, contacts);
  - OpenStreetMap Nominatim (HQ geocoding).

  Every client is injectable, so the whole test suite runs offline.
- **Scoring** (`backend/app/scoring/` for businesses, `backend/app/screening/`
  for individuals) is plain, rule-based code. Every weight and threshold is
  explicit. No ML, no LLM, no black box. Screening weights and thresholds are
  versioned data (rule versions), so a change never rewrites a past decision.
- **Append-only audit.** The audit log and screening decisions reject UPDATE
  and DELETE at the database layer (triggers on Postgres and SQLite), not
  just in code.
- **Stack:**
  - FastAPI, SQLAlchemy/Alembic and Celery;
  - Postgres (SQLite in dev and tests);
  - Redis-backed operator sessions, with an in-memory fallback;
  - React 18 + TypeScript + Vite.

## Quality

- **1007 backend tests** and **108 frontend tests**, plus ruff, eslint and tsc,
  all run in [GitHub Actions](.github/workflows/ci.yml).
- **Screening release gates in CI**, measured over a versioned, fictional
  adversarial name corpus:
  - blocking recall must stay at 100%;
  - decision reproducibility must stay at 100%.

  CI also publishes the false-positive rate at full recall and the REVIEW
  rate as a report.
- A golden fixture pins the screening engine's output for every corpus case,
  so a refactor can't silently change a verdict.
- An end-to-end test drives the real business-verification stage list with
  only network clients faked. It exists because unit tests alone once missed
  that the adapters and the scoring disagreed on field names.
- The demo dataset's expected triage tiers and screening outcomes are pinned
  by tests.

## Deployment

A private (IAM-only) demo instance on Google Cloud Run with Cloud SQL
Postgres. It's one image serving the UI and the API; migrations and the seed
run as Cloud Run Jobs; secrets come from Secret Manager. The decision is
[ADR-0005](docs/decisions/0005-cloud-run-hosting.md). Steps are in
[docs/runbooks/deploy-cloud-run.md](docs/runbooks/deploy-cloud-run.md). The
first deploy is paused partway through; the runbook has the resume command.

## Status and known gaps

The business-verification core is complete. Individual screening v1
(deterministic, official sanctions lists) is complete. What's still open:

- **Registry data needs a key and a license.** OpenCorporates requires an API
  token (`OPENCORPORATES_API_TOKEN`); without one, the registry source shows as
  *unavailable*. Production use also needs a data license.
- **Registry officers and owners aren't wired to a live source yet.** Declared
  officers and owners are screened today; pulling them from OpenCorporates or
  UK Companies House (officers and persons with significant control) needs
  credentials, so the registry part shows as *unavailable*. There's no public
  US beneficial-ownership source. See
  [ADR-0006](docs/decisions/0006-officer-screening-bridge.md).
- **Tax ID and LinkedIn run on stub providers.** The live vendors are waiting
  on a provider decision and a legal sign-off. See
  [docs/PRD-identity-corroboration.md](docs/PRD-identity-corroboration.md).
- **Screening covers official sanctions lists only.** PEP lists, adverse
  media and a calibrated name-judgment model are deferred until the data is
  licensed and a model provider is chosen.
- **Screening gaps against the requirements** are mapped in
  [the spec-gap plan](docs/plans/2026-09-25-001-individual-screening-spec-gaps-plan.md).
  The main ones:
  - per-jurisdiction thresholds;
  - a lead-facing way to change rules;
  - day-to-day metrics such as alerts per 1,000 screenings and
    time-to-disposition;
  - a regression suite of confirmed failures;
  - a known recall gap: first names spelled with "th" vs "t" (Theodor vs
    Teodor) don't match.
- **Screening thresholds aren't tuned on labeled data yet.** Under the
  default rule, a name plus a full date of birth lands in REVIEW, not MATCH.

The business-verification audit, including a live-data run, is in
[docs/ASSESSMENT-2026-09-24.md](docs/ASSESSMENT-2026-09-24.md).

## Documentation

- [docs/problem-statement.md](docs/problem-statement.md): the problem
- [docs/PRD.md](docs/PRD.md): business-verification requirements
- [docs/identity-verification-PRD.md](docs/identity-verification-PRD.md): individual-screening requirements
- [docs/PRD-identity-corroboration.md](docs/PRD-identity-corroboration.md): tax-ID and LinkedIn corroboration
- [docs/STRATEGY.md](docs/STRATEGY.md): approach, metrics, tracks
- [docs/USERS.md](docs/USERS.md): operators, leads, integrating systems, registrants
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): technical design and decisions
- [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) and [docs/BUILD_PLAN-individual-screening.md](docs/BUILD_PLAN-individual-screening.md): build plans and status
- [docs/plans/](docs/plans/): feature plans, including the decision evidence panel
- [docs/decisions/](docs/decisions/): ADRs (PII retention, screening libraries, crypto-shred retention, Cloud Run hosting)
- [docs/RUNBOOK.md](docs/RUNBOOK.md) and [docs/runbooks/](docs/runbooks/): setup, list ingestion and monitoring, full-stack mode, deployment
- [docs/implementation-notes.md](docs/implementation-notes.md): dated decision log
