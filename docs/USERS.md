# Users

Who interacts with EntityIQ and how. This documents the people and systems that
use the platform, the job each is trying to do, and the interface surface each
touches. Grounded in [STRATEGY.md](./STRATEGY.md), [PRD.md](./PRD.md), and
[problem-statement.md](./problem-statement.md).

## At a glance

| Actor | Type | Primary interface | What they do |
| --- | --- | --- | --- |
| Verification operator | Human (co-primary user) | Operator web app (dashboard/workbench) | Review registrations, decide approve/reject, correct data, re-run analysis |
| Integrating systems | Machine (co-primary user) | REST API | Submit registrations, pull reports, trigger re-analysis |
| Compliance lead / manager | Human (secondary) | Operator web app (oversight + audit views) | Set policy, monitor consistency, defend decisions in audit |
| Enterprise registrant | Human (subject, not an operator) | Host platform's onboarding flow + domain-ownership challenges | Submits registration data; optionally proves domain control |

> EntityIQ has **two co-primary users** — the human operator and the integrating
> systems. When their needs conflict (e.g. operator UX vs. API ergonomics), the
> strategy does not yet name a tiebreaker; this is an open decision flagged in
> `STRATEGY.md`.

## 1. Verification operator (primary)

**Who:** A frontline compliance operator at a B2B platform facing a queue of self-registered
enterprise accounts, each needing an approve/reject call before the customer gets
enterprise features.

**Job to be done:** Decide — quickly and defensibly — whether a business is real,
correctly represented, and low-risk, with evidence they can stand behind in an audit.

**What they need from the system:** a triaged queue (risky cases surfaced, low-risk
cases pre-cleared), an explainable risk assessment with source attribution, clear
submitted-vs-discovered diffs, and the ability to act on and document their decision.

### How they interface

The operator works entirely through the **operator web application**. They never
make decisions blind: the system does the evidence gathering and scoring; the
operator decides.

- **Sign in** with an individual account; every action is audited.
- **Dashboard / queue** — list of analyzed companies with analysis date, risk
  score, and review status; filter and search.
- **Company detail view** — registration data (submitted vs. discovered, with
  match/mismatch indicators), DNS & domain intelligence, registry information,
  contact information with source attribution, HQ map, and network/IP intelligence.
- **Risk assessment panel** — overall 0–100 score, the four-layer confidence
  breakdown, contributing signals, and risk flags, each explainable.
- **Operator actions** — mark reviewed, re-run analysis, correct submitted data
  (then re-analyze), add review notes, export the report. All actions are audited.

## 2. Integrating systems (co-primary)

**Who:** The host platform's self-service onboarding flow plus other internal systems that need
verification programmatically — not a person, a delivery and consumption channel.

**Job to be done:** Submit a registration and get back a structured, queryable risk
report that downstream systems can act on programmatically.

**What they need from the system:** a stable ingestion contract, a machine-readable
report (scores, evidence, mismatches, sources), and a way to trigger re-analysis.

### How they interface

Integrating systems interact entirely through the **REST API**. No human UI.

- **Submission endpoint** — send registration data: company name, domain, work
  email, country, and optional fields (tax ID / registration number, billing
  address, phone, requester name/metadata, LinkedIn URL). Requester **network
  metadata** (source IP, user agent, request timestamp, forwarded headers,
  submission endpoint metadata) is captured server-side at submission time.
- **Report retrieval endpoint** — fetch the normalized report: evidence, layered
  scores, mismatches, and source attribution. Partial/intermediate results are
  retrievable while analysis is still running.
- **Re-analysis endpoint** — trigger a fresh verification run for an entity.
- **Operator workflow endpoints** — supporting endpoints that back review state.

> Network metadata such as `X-Forwarded-For` is client-spoofable; the platform
> treats the trusted edge/proxy-assigned IP as authoritative, not raw headers.

## 3. Compliance lead / manager (secondary)

**Who:** The risk/compliance lead who owns verification policy and is accountable
for the audit trail across the operator team.

**Job to be done:** Ensure decisions are consistent and defensible regardless of
which operator made them, and stand behind any approval to a regulator.

### How they interface

Through the **operator web application**, using the same dashboard plus the
auditability surface:

- Review the **audit log** of operator actions, verification runs, risk-score
  changes, evidence sources used, and re-analysis history.
- Monitor consistency across operators (supports the decision-consistency metric).
- Spot-check or re-open individual cases.

## 4. Enterprise registrant (subject — not an operator)

**Who:** The person registering their company for the host platform's enterprise features. They
are the **subject** of verification, not a user of the operator tool. They are also
the actor whose claims and network footprint the platform scrutinizes for fraud,
impersonation, and sanctions-evasion signals.

**Job to be done (their side):** Get their legitimate company approved for
enterprise access with minimal friction.

### How they interface

Indirectly, through the host platform's existing **self-service onboarding flow** — which feeds
EntityIQ via the submission API. The registrant does not log into EntityIQ. Their
only direct touchpoint is **optional domain-ownership verification**, which EntityIQ
may request to raise confidence:

- **Email verification** — confirm control of an address at the claimed domain.
- **DNS TXT verification** — publish a provided token as a DNS TXT record.
- **HTML meta-tag verification** — place a provided tag on the claimed website.

> Domain-ownership verification **increases confidence** but does **not** prove the
> registrant is *authorized* to represent the organization. EntityIQ produces
> evidence and confidence signals for human-assisted verification; it does not
> assert legal authorization.

## Access, permissions & audit

- **Operators and compliance leads** authenticate with individual accounts. Every
  action they take is recorded in an immutable audit trail.
- **Integrating systems** authenticate at the API boundary (service credentials);
  submissions and report pulls are attributable to the calling system.
- **Registrants** are never granted access to EntityIQ's operator surfaces; they
  only see the host platform's onboarding flow and any domain-ownership challenge presented to
  them.
- The platform records operator actions, verification runs, risk-score changes,
  evidence sources used, and re-analysis history for auditability.

## Boundaries

- EntityIQ **augments** the operator's decision; it does not auto-approve accounts
  or replace human compliance review.
- It is a risk-reduction and confidence-assessment platform, not a guarantee of
  fraud prevention or a definitive authorization check.
