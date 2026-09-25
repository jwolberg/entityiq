# Architecture

Lightweight architecture for EntityIQ. Records the structure and the decisions
needed to start planning — it does not design every class. Detail is deferred to
each track's spec. Grounded in [STRATEGY.md](./STRATEGY.md), [PRD.md](./PRD.md),
and [USERS.md](./USERS.md).

> **Status:** draft. Several choices below are **proposed** (see
> [Open decisions](#open-decisions)) and should be confirmed before `/plan`.

## 1. System context & components

EntityIQ is a monorepo with a React/TypeScript operator web app, a FastAPI
backend, an async verification pipeline, and a Postgres store. Both co-primary
users (the operator UI and integrating systems) enter through the same API layer.

```
   Enterprise registrant
            │ (registers)
            ▼
   Host onboarding flow  ─┐                  Operator web app (React/TS)
   Other internal systems ─┤                          │  ▲
            (submit │ ▲ report)                 (actions │ │ data)
                    ▼ │                                  ▼ │
            ┌───────────────────────────────────────────────┐
            │                 API layer (FastAPI)            │
            │   submission · report · re-analysis · workflow │
            └───────────────────────────────────────────────┘
                  │ enqueue run                ▲ read report
                  ▼                            │
            ┌──────────┐   dispatch    ┌────────────────────┐
            │  Queue   │ ────────────► │ Verification        │
            │ (Redis)  │               │ pipeline (workers)  │
            └──────────┘               └────────────────────┘
                                         │ fetch        ▲ persist
                                         ▼              │ evidence/scores
                         External sources         ┌──────────────────┐
                         · business registries    │     Postgres     │
                         · IPinfo (network)        │ entities,        │
                         · WHOIS / DNS / SSL        │ evidence, runs,  │
                         · sanctions/watchlists     │ reports, scores, │
                         · public web (fallback)    │ audit log        │
                                                    └──────────────────┘
```

**Components**

- **Operator web app** — dashboard/queue, company detail, risk panel, operator
  actions. Calls the API; holds no business logic of its own.
- **API layer** — the single entry point for humans (via the web app) and machines.
  Serves submission, report retrieval, re-analysis, and operator-workflow endpoints.
- **Queue + workers** — async job execution so a submission returns immediately and
  analysis runs in the background; supports the PRD's < 2h target and
  partial-result visibility.
- **Verification pipeline** — the agentic flow (§2) that gathers evidence, checks
  consistency, and scores risk.
- **Scoring & explainability engine** — composes evidence into the four-layer
  confidence assessment and the 0–100 score, retaining the signal-to-evidence links.
- **Postgres** — system of record for entities, evidence, runs, reports, scores,
  and the immutable audit log.

## 2. Agentic verification pipeline

The pipeline runs as an async job per verification run, executing the PRD's nine
stages. It is **authoritative-first**: structured sources lead; open-web scraping
is fallback enrichment only.

1. **Normalize input** — clean and canonicalize submitted fields (domain, country,
   address, tax ID format by country).
2. **Resolve entity candidates** — match the submission to candidate real-world
   entities.
3. **Query authoritative registries** — business registries, OpenCorporates,
   tax-ID/FEIN verification (US), sanctions/watchlists (Tier 1).
4. **Analyze domain infrastructure** — WHOIS age, DNS, MX, SPF/DKIM, SSL, registrar
   reputation (Tier 2).
5. **Enrich network/IP** — IPinfo on the captured submission IP: geo, ASN/ISP,
   hosting/VPN/proxy flags, reuse patterns.
6. **Crawl/extract public web evidence** — site, contacts, directories, press
   footprint, LinkedIn company page via a licensed provider, never scraped
   (Tier 3, fallback).
7. **Consistency checks** — submitted vs. discovered field comparisons; cross-source
   mismatches; risk/trust signals.
8. **Generate risk assessment** — four-layer scores + overall 0–100 + flags + triage
   tier, each with evidence attribution.
9. **Store report & serve** — persist; expose to API consumers and the web app.

**Execution properties**

- **Per-stage isolation** — each stage writes its evidence independently, so a
  failed/slow source degrades gracefully rather than failing the whole run.
- **Partial results** — the report is readable mid-run; each section carries a
  status (pending / complete / unavailable). Required by the PRD.
- **Retries & timeouts** — per external call, with backoff; a source that stays
  down is recorded as `unavailable` and excluded from scoring confidence.
- **Re-analysis** — a new run supersedes the prior one for an entity; prior runs are
  retained for the audit trail and score-change history.

## 3. Data model (sketch)

Indicative entities and key fields — not final schema.

- **`submission`** — raw submitted input (company name, domain, work email, country,
  optional tax ID / billing address / phone / requester metadata / LinkedIn) plus
  **network metadata** (source IP, user agent, timestamp, forwarded headers,
  endpoint). Immutable record of what arrived.
- **`entity`** — the canonical company under analysis; links submissions and runs.
- **`verification_run`** — one analysis execution: status, started/finished, the
  superseded run, source-availability summary.
- **`evidence`** — one finding from one source: `source`, `tier`, `field`,
  `raw_value`, `normalized_value`, `confidence`, `fetched_at`, attribution. The unit
  of explainability.
- **`field_comparison`** — submitted vs. discovered value + match status
  (match / mismatch / unverified) for the UI's diff view.
- **`risk_assessment`** — per run: four layer scores (entity, infrastructure,
  representation, risk), overall 0–100, triage tier, and the list of contributing
  signals (each referencing `evidence` rows).
- **`report`** — queryable composite of a run for API/FE consumers.
- **`operator`** — operator/lead account + role.
- **`review`** — operator decision on a run: status (reviewed), notes, corrections,
  decided_by.
- **`audit_event`** — append-only log of every operator action, run, score change,
  source used, and re-analysis.

## 4. External integrations

Each external source sits behind a **uniform adapter interface** that returns
normalized `evidence` with a confidence and source attribution. This isolates the
pipeline from per-source quirks and the PRD's known "scraping brittleness" risk.

- **Adapter contract** — `fetch(entity_context) -> [evidence]`; failures are typed
  (timeout / unavailable / not-found / rate-limited), never silent.
- **Caching** — keyed by `(source, lookup_key)` with per-source TTL, to bound cost
  and latency on re-analysis.
- **Rate limiting & backoff** — per source, respecting provider quotas.
- **Graceful degradation** — an unavailable source yields a `unavailable` section,
  not a failed run; scoring accounts for reduced coverage.
- **Source tiers** — Tier 1 authoritative (registries, sanctions, OpenCorporates,
  tax ID/FEIN), Tier 2 infrastructure (WHOIS/DNS/SSL, IPinfo), Tier 3 public web
  (Playwright fallback, LinkedIn). Identity sources default to an unconfigured
  provider (source unavailable) until Open Decision #5 selects vendors. Authoritative sources are tried first and weighted highest.

## 5. Authentication & authorization

- **Operators / leads** — sign in via OIDC/SSO against the host organization's IdP; session-based.
  **RBAC**: `operator` (review, correct, re-run) vs `lead` (oversight + audit) vs
  `examiner` (read-only: all views, the global audit log, and screening replay;
  an app-wide guard returns 403 on every other write).
  Every action is attributed and audited.
- **Integrating systems** — authenticate at the API boundary with service
  credentials (API keys to start; mTLS as a later option). Submissions and report
  pulls are attributable to the calling system for audit.
- **Registrants** — no access to EntityIQ surfaces; their only touchpoint is
  optional domain-ownership challenges issued via the onboarding flow.
- **Trust boundary** — `X-Forwarded-For` and similar are client-spoofable; the
  trusted edge/proxy-assigned IP is authoritative for network intelligence.

## 6. Cross-cutting concerns

- **Auditability** — append-only `audit_event`; runs and score changes are never
  overwritten, satisfying the PRD's audit requirements.
- **Explainability** — every score traces to the `evidence` rows that produced it;
  no signal contributes to a score without an attributable source.
- **Partial results** — report sections are independently readable and status-tagged.
- **Idempotency** — submission endpoint is idempotent on a client-supplied key to
  tolerate retries from integrating systems.
- **PII handling** — submissions, contact data, and network metadata are personal
  data; access is role-gated, audited, and subject to the retention policy in
  [ADR-0002](./decisions/0002-pii-retention-policy.md).

## Open decisions

These shape planning and should be confirmed. Each names a **recommendation** and
the alternative. Decisions #1–#3 were **resolved 2026-05-26** (see
[implementation-notes.md](./implementation-notes.md)).

| # | Decision | Recommendation | Why / alternative |
| --- | --- | --- | --- |
| 1 | Backend stack | **Python + FastAPI** ✅ resolved 2026-05-26 | Verification, web extraction (Playwright), and parsing/normalization (addresses, phone, WHOIS, DNS, sanctions) have the strongest ecosystem in Python — and these *are* the product (Tracks 1–2). Alt: **Node/TS** unifies language with the FE and `shared/`, at the cost of a weaker enrichment ecosystem. |
| 2 | Job orchestration | **Celery + Redis** ✅ resolved 2026-05-26 | Mature, simple, fits Python and the async-run model. Alt: RQ (lighter) or Temporal (durable workflows, heavier) if pipeline complexity grows. |
| 3 | `shared/` contents | **OpenAPI-generated TS types** ✅ resolved 2026-05-26 | If BE is Python, the shared contract is the API schema, with TS types generated for the FE — not shared runtime code. Revisit if stack #1 flips to Node/TS. |
| 4 | HQ map provider | **Resolved 2026-09-24:** OpenStreetMap embed (iframe, no key/SDK) + Nominatim geocoding | Chosen for zero cost/keys in a demo. Nominatim policy: identifying User-Agent, ≤1 req/s — fine per submission, not for bulk; swap to a paid geocoder (Mapbox/Google) at scale. |
| 5 | Data-source access | _Undecided_ | Registries, OpenCorporates, sanctions APIs, LinkedIn have real licensing/ToS limits (flagged in problem-statement.md). Confirms which Tier 1 sources are actually obtainable before pipeline design. |
| 6 | Co-primary tiebreaker | _Unresolved_ | When operator-UX and API-consumer needs conflict, which wins? Carried from `STRATEGY.md`. |
| 7 | PII retention policy | **Resolved 2026-09-24**: [ADR-0002](./decisions/0002-pii-retention-policy.md). Network metadata 90d, then /24; PII 5y after review / 180d if never reviewed; anonymize, never delete; audit kept. | How long submissions, contacts, and network metadata are retained, and who may access them. |
