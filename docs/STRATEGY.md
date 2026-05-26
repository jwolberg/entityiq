---
name: EntityIQ
last_updated: 2026-05-26
---

# EntityIQ Strategy

## Target problem

When enterprises self-register for SkyFi, operators must decide whether each
business is real, correctly represented, and safe to approve — but manual review
is both unreliable (evidence is fragmented across inconsistent global registries
with no single source of truth) and unscalable (capped by operator time, with
quality depending on who happens to review). Two operators can see the same
registration and reach different verdicts.

## Our approach

We win by making the operator's verdict fast and confident rather than by removing
the operator: synthesize authoritative-first, multi-source evidence into a layered,
explainable confidence assessment, then triage the queue — pre-clearing low-risk
cases for one-click human sign-off and escalating the rest. Throughput comes from
cutting decision time, not decision-makers; we explicitly reject "scrape everything
first," single-source trust, and black-box scores.

## Who it's for

**Primary:** Compliance / verification operator — hiring EntityIQ to decide,
quickly and defensibly, whether a self-registered enterprise account is real,
correctly represented, and low-risk, with evidence they can stand behind in an audit.

**Primary (co-equal):** Integrating systems — the onboarding flow and other internal
systems hiring EntityIQ's API to submit registrations and consume structured,
queryable risk reports programmatically.

_Tiebreaker unresolved: when operator-UX and API-consumer needs conflict, the doc
does not yet name which wins. Revisit._

## Key metrics

- **Triage precision & recall** — of cases pre-cleared low-risk, % a human confirms
  low-risk (precision); of truly-risky cases, % flagged elevated (recall). Measured
  from operator overrides vs. system risk tier.
- **Inter-operator decision consistency** — agreement rate when two operators (or
  operator vs. system tier) assess the same case. Measured via periodic double-review
  sampling.
- **Escaped-fraud / false-approval rate** — of approved accounts, % later found
  fraudulent, impersonated, or sanctioned. Measured from post-hoc review and
  compliance incidents; the real-world accuracy guard.

_Watch-item: no headline throughput metric (e.g. median operator decision time)
yet — revisit if the scale half of the problem can't be proven._

## Tracks

### Evidence & enrichment pipeline

The agentic multi-source collection and verification engine: authoritative registries,
domain/infrastructure intelligence, network/IP enrichment, and public-web evidence —
authoritative-first, scraping only as fallback.

_Why it serves the approach:_ synthesis needs broad, source-attributed evidence.

### Risk scoring & explainability

The layered confidence model (entity, infrastructure, representation, risk),
match/mismatch detection, source attribution, and the triage tiering that decides
what's pre-cleared vs. escalated.

_Why it serves the approach:_ this is the triage-and-explainability bet itself.

### Operator workbench

The audited dashboard: company queue and filters, detail view with
submitted-vs-discovered diffing, HQ map, data corrections and re-analysis,
one-click sign-off, and a full audit trail.

_Why it serves the approach:_ "human decides" — serves co-primary user #1.

### Integration & reporting API

Ingestion endpoint, queryable report API, re-analysis and workflow endpoints, and
the auditability surface for downstream systems.

_Why it serves the approach:_ serves co-primary user #2.
