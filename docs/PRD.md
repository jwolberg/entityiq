# PRD — Enterprise Business Verification & Risk Intelligence Platform

## Overview

Build an AI-assisted enterprise verification platform that evaluates whether:

- A business entity appears legitimate and active
- The submitted registration information matches public evidence
- The requester plausibly represents the organization
- The registration presents elevated fraud, sanctions-evasion, or impersonation risk

The system ingests registration data from a B2B platform's self-service enterprise onboarding flow, performs automated multi-source verification and enrichment, and produces an explainable risk assessment report for human operators.

The platform is intended to reduce manual review effort, improve consistency and accuracy, and identify suspicious or staged business identities.

## Problem Statement

B2B platforms increasingly let enterprise customers self-register for enterprise features.

This creates several risks:

- Non-existent companies
- Fraudulent impersonation of real companies
- Unauthorized individuals acting on behalf of companies
- Staged or fabricated internet presence
- Sanctions-evasion attempts
- Low-quality manual verification decisions

Current verification is manual, slow, inconsistent, and dependent on operator expertise.

Additionally:

- No universal global business registry exists
- Registries vary by country
- Public business data quality varies widely

The platform needs an automated intelligence and risk assessment system that:

- Collects evidence from authoritative and public sources
- Verifies consistency between submitted and discovered information
- Produces explainable confidence/risk scoring
- Escalates suspicious cases for human review

## Goals

### Primary Goals

- Reduce manual review time
- Improve consistency and accuracy of enterprise verification
- Detect suspicious or staged business identities
- Surface explainable evidence for operators
- Create auditable verification workflows

### Secondary Goals

- Create reusable APIs for integration into other systems
- Support future automation and scaling
- Build maintainable architecture suitable for long-term evolution

### Non-Goals

The system does NOT attempt to:

- Provide definitive legal authorization verification
- Fully automate all compliance decisions
- Replace human compliance review entirely
- Guarantee fraud prevention

The system is a **risk reduction and confidence assessment platform**.

## Core Product Concept

The platform operates as:

- An ingestion API
- An evidence collection and verification pipeline
- A risk scoring engine
- An operator review dashboard
- A reporting API

### Key Product Insight

The platform should prioritize:

- Multi-source evidence synthesis
- Cross-consistency validation
- Explainable scoring
- Confidence assessment

NOT:

- Generic internet scraping
- Single-source validation
- Black-box AI outputs

## Core Verification Philosophy

Verification is divided into four independent confidence layers:

| Layer | Question |
| --- | --- |
| Entity Legitimacy | Does this business appear to exist? |
| Infrastructure Legitimacy | Does the organization control the claimed domain and communications infrastructure? |
| Representation Confidence | Does the requester plausibly represent the organization? |
| Risk Signals | Are there signs of fraud, staging, impersonation, or sanctions evasion? |

## Inputs

### Required Inputs

| Field | Notes |
| --- | --- |
| Legal company name | Primary lookup identifier |
| Work email | Must not be disposable/free-email-only for enterprise approval |
| Company domain | Primary infrastructure anchor |
| Country | Narrows registry search space |

### Optional Inputs

| Field | Purpose |
| --- | --- |
| Tax ID / Registration Number | Strong registry validation |
| Billing address | Registry consistency |
| Phone number | Contact verification |
| Requester full name | Employee/leadership matching |
| LinkedIn URL | Supporting evidence |
| Additional company metadata | Optional enrichment |

## Verification Sources

### Tier 1 — Authoritative Sources

Highest confidence sources.

**Examples**

- Government business registries
- OpenCorporates
- Structured business databases
- Sanctions/watchlist APIs

**Purpose**

- Verify business existence
- Registration status
- Incorporation metadata
- Legal addresses
- Tax identifiers

### Tier 2 — Infrastructure Signals

**Domain Intelligence**

- WHOIS age
- Domain registration recency
- Registrar reputation
- SSL certificate age
- MX records
- SPF/DKIM presence
- DNS anomalies

**Purpose**

Detect:

- Recently staged domains
- Disposable infrastructure
- Thin/fraudulent web presence

### Tier 3 — Public Web Presence

**Sources**

- Company website
- Contact pages
- LinkedIn
- Public directories
- Press/news footprint

**Purpose**

Validate:

- Branding consistency
- Employee footprint
- HQ presence
- Contact consistency
- Historical presence

## Risk Signals

### Elevated Risk Indicators

- Recently registered domain
- No MX records
- Disposable/free email domains
- Registry mismatch
- Domain-country mismatch
- Billing address mismatch
- Thin/generated website
- No employee footprint
- Recently created social presence
- Multiple conflicting company identities
- Suspicious DNS infrastructure
- Inconsistent contact information

### Trust Signals

**Positive Indicators**

- Long-lived domain
- Registry confirmation
- Consistent addresses
- Valid tax ID
- Active employee footprint
- Matching contact information
- Domain ownership verification
- Stable web presence

## Domain Ownership Verification

The system should support optional:

- Email verification
- DNS TXT verification
- HTML meta tag verification

**Important distinction:**

Domain ownership verification increases confidence but does NOT prove organizational authorization.

## Network & IP Intelligence

The system must capture requester network metadata during enterprise registration and include it in the verification report.

**Required captured fields:**

- Source IP address
- User agent
- Request timestamp
- Forwarded headers, where available
- Submission endpoint metadata

The backend will enrich the IP address using IPinfo or equivalent provider.

**The report should include:**

- IP country/region/city
- ASN / ISP
- Organization name
- Hosting/VPN/proxy indicators where available
- Distance/mismatch from claimed company or billing location
- Reuse patterns across submissions

**The risk model should flag:**

- IP country mismatch with company/billing country
- Datacenter or anonymized network usage
- Repeated registrations from same IP/ASN
- Suspicious network ownership
- Unusual geography relative to submitted company identity

## Risk Scoring

The system produces:

### Overall Risk Score

0–100

### Confidence Breakdown

| Category | Score |
| --- | --- |
| Entity legitimacy | |
| Infrastructure legitimacy | |
| Representation confidence | |
| Fraud/staging risk | |

### Explainability Requirements

Every risk score MUST:

- Show evidence
- Show source attribution
- Explain contributing signals
- Highlight mismatches visually

The operator should understand:

- WHY the score exists
- WHAT caused concern
- WHICH sources were used

## Frontend Requirements

### Operator Authentication

- Operators authenticate into dashboard
- Actions are audited

### Dashboard

Display:

- Company list
- Analysis date
- Risk score
- Review status
- Filters/search

### Company Detail View

**Registration Data**

Show:

- Submitted values
- Discovered values
- Match/mismatch indicators

**DNS & Domain Intelligence**

Show:

- Domain age
- Registrar
- MX/SPF/DKIM
- SSL metadata
- DNS risk score

**Registry Information**

Show:

- Company registration status
- Jurisdiction
- Registration identifiers
- Legal address

**Contact Information**

Show:

- Names
- Phones
- Emails
- Addresses
- Source attribution

**HQ Visualization**

- Embedded map
- Address confidence

**Risk Assessment**

- Overall score
- Evidence summary
- Risk flags
- Operator notes

### Operator Actions

- Mark reviewed
- Re-run analysis
- Correct submitted data
- Add review notes
- Export report

All actions must be audited.

## Backend Requirements

### REST API

Provide:

- Company submission endpoint
- Report retrieval endpoint
- Re-analysis endpoint
- Operator workflow endpoints

### Agentic Verification Pipeline

Pipeline stages:

1. Normalize input
2. Resolve entity candidates
3. Query authoritative registries
4. Analyze domain infrastructure
5. Crawl/extract public web evidence
6. Perform consistency checks
7. Generate risk assessment
8. Store report
9. Serve FE/API consumers

## Suggested Architecture

### Monorepo

Required:

```
frontend/
backend/
shared/
docs/
tests/
```

### Recommended Stack

**Frontend**

- React + TypeScript
- Tailwind or minimal UI framework

**Backend**

- FastAPI or Node/TypeScript
- Async job orchestration

**Storage**

- Postgres

**Queue/Jobs**

- Celery / BullMQ / Temporal-lite pattern

**Search & Extraction**

- Playwright for fallback scraping
- Structured APIs preferred first
- LLM-assisted extraction only where needed

### Important Architectural Principle

The system should prioritize:

1. Structured authoritative data
2. Infrastructure verification
3. Public evidence enrichment
4. Scraping as fallback enrichment only

NOT:

- "Scrape everything first."

## Performance Expectations

- Accuracy prioritized over speed
- Typical analysis target: < 2 hours per company
- Partial/intermediate results should be viewable during processing

## Auditability Requirements

The system must record:

- Operator actions
- Verification runs
- Risk score changes
- Evidence sources used
- Re-analysis history

## API Requirements

### Submission Endpoint

Accept:

- company name
- domain
- email
- address
- tax ID
- phone
- requester metadata

### Report Endpoint

Return:

- normalized report
- evidence
- scores
- mismatches
- sources

## Success Criteria

### Operational Success

- Reduce manual review effort
- Improve operator consistency
- Surface suspicious registrations faster

### Technical Success

- Clean maintainable monorepo
- Strong documentation
- Test coverage
- Explainable outputs
- Reliable enrichment pipeline

### Product Success

Operators should be able to understand risk quickly and make faster, higher-confidence approval decisions.

## Explicit Risks & Constraints

### Known Challenges

- Global registry fragmentation
- Varying data quality
- Inconsistent international formats
- False positives
- Scraping brittleness
- Authorization ambiguity

### Key Product Insight

The system should explicitly recognize:

The platform cannot definitively prove organizational authorization.

Instead, it produces:

- evidence
- consistency checks
- confidence signals
- explainable risk assessment

for human-assisted enterprise verification.
