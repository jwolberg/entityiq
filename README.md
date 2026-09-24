# EntityIQ

**Business verification and risk intelligence for self-service enterprise sign-ups.**

When a company registers itself for an enterprise account, someone has to decide
whether it's real, whether it's who it says it is, and whether it's safe to
approve. EntityIQ gathers evidence from registries, sanctions lists, domain and
email infrastructure, network data, and the company's own website. It checks
what was submitted against what it found and produces a **deterministic,
explainable risk score** that sorts the review queue. It never approves anyone:
a human makes every decision, and every action is audited.

![Verification queue](docs/img/dashboard.png)

## Quick start

```bash
./scripts/demo.sh
```

Needs Python 3.11+ and Node 20+. No Docker, Postgres, or Redis. The script sets
up everything on first run, loads six fictional companies that span every risk
tier, and serves:

- **UI:** http://localhost:5173. Sign in as `lead@demo.entityiq.dev` (or
  `operator@demo.entityiq.dev`); the password is `entityiq-demo`.
- **API docs:** http://localhost:8000/docs

Demo companies run through the real pipeline with recorded source responses, so
the demo is deterministic and works offline. Details are in
[docs/RUNBOOK.md](docs/RUNBOOK.md).

## What it does

| | |
|---|---|
| **Four-layer scoring** | Entity legitimacy, infrastructure legitimacy, representation confidence, and fraud/staging risk. Each is scored 0–100 and combined into an overall score. |
| **Triage, not verdicts** | Every run lands in `pre_clear`, `review`, or `escalate`. Critical signals (such as a sanctions match) force `escalate` whatever the score. Nothing is ever auto-approved. |
| **Explainable** | Every signal cites the evidence rows behind it, with source attribution. An unavailable source *lowers confidence*; it's never counted as "low risk". |
| **Operator workbench** | Submitted-vs-discovered diff, domain/DNS, registry, contacts, HQ map with address confidence, risk flags, review and notes, correct-and-re-run, JSON export, and a per-case activity timeline. |
| **Integration API** | `POST /submissions` (API key or operator token, idempotent), report read and export, re-analysis. Trusted-edge IP capture: a client can't spoof its own IP via `X-Forwarded-For`. |
| **Audit** | Append-only log of every operator and integration action. Leads get a global Audit Log view. |

<p>
  <img src="docs/img/detail-volga.png" width="49%" alt="Company detail — sanctions escalation" />
  <img src="docs/img/audit-log.png" width="49%" alt="Lead audit log" />
</p>

![HQ map with address confidence](docs/img/hq-map.png)

## How it works

```
POST /submissions ──► FastAPI ──► Submission + VerificationRun (pending)
                                          │  Celery (Redis, or inline in dev)
                                          ▼
  normalize → resolve → registries → sanctions → domain → IP → web
            → consistency → geocode HQ → scoring → report
                                          │
      Operator UI (React) ◄── GET /reports ┘      every stage isolated:
                                                   a failed source is marked
                                                   "unavailable", the run continues
```

- **Sources.** OpenCorporates (registry), OFAC SDN (sanctions), WHOIS/DNS/SSL
  (domain, MX, SPF, DKIM), IPinfo (geo, ASN, VPN/hosting), the company website
  (branding, contacts), and OpenStreetMap Nominatim (HQ geocoding). Every
  client is injectable, so the whole test suite runs offline.
- **Scoring** (`backend/app/scoring/`) is plain, rule-based code: each signal
  has an explicit weight and direction. No ML, no LLM, no black box.
- **Stack:** FastAPI, SQLAlchemy/Alembic, Celery, Postgres (SQLite in dev and
  tests), React 18 + TypeScript + Vite.

## Quality

- **439 backend tests** and **30 frontend tests**, plus ruff, eslint, and tsc,
  all run in [GitHub Actions](.github/workflows/ci.yml).
- An end-to-end test drives the real stage list with only network clients
  faked. It exists because unit tests alone once missed that the adapters and
  the scoring disagreed on field names (see the assessment below).
- The demo dataset's expected triage tiers are pinned by tests, so a scoring
  change can't silently re-tier the demo.

## Status and known gaps

The core product is complete. What's still open is tracked in
[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md). The most notable items:

- **Registry data needs a key and a license.** OpenCorporates requires an API
  token (`OPENCORPORATES_API_TOKEN`); without one, the registry source shows as
  *unavailable*. Production use also needs a data license.
- **Some PRD signals aren't built yet:** LinkedIn/social presence, press and
  directories, suspicious-DNS detection, and domain-ownership verification.
  Tax-ID (FEIN) and LinkedIn corroboration are planned in
  [docs/PRD-identity-corroboration.md](docs/PRD-identity-corroboration.md).
- **Sessions are in-memory.** That suits a single-process demo; production
  needs a shared store.

The requirement-by-requirement audit, including a live-data run, is in
[docs/ASSESSMENT-2026-09-24.md](docs/ASSESSMENT-2026-09-24.md).

## Documentation

- [docs/problem-statement.md](docs/problem-statement.md): the problem
- [docs/PRD.md](docs/PRD.md): product requirements
- [docs/STRATEGY.md](docs/STRATEGY.md): approach, metrics, tracks
- [docs/USERS.md](docs/USERS.md): operators, leads, integrating systems, registrants
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): technical design and decisions
- [docs/RUNBOOK.md](docs/RUNBOOK.md): setup, configuration, full-stack mode
- [docs/implementation-notes.md](docs/implementation-notes.md): dated decision log
