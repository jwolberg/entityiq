---
id: 0002
title: PII retention and access policy (Open Decision #7)
anchor: ADR-0002
status: accepted
date: 2026-09-24
supersedes:
superseded-by:
---

Settles ARCHITECTURE Open Decision #7 so that P3-T3 (ticket 0002) can go ahead.
The user asked for a default value. All of these durations are env-configurable
defaults, not legal advice. Revisit them before any real deployment with actual
compliance counsel.

## [1] Context

Submissions contain personal data: the requester's name, email, phone, billing
address, LinkedIn URL and tax ID, and the network metadata captured server-side
(`source_ip`, `user_agent`, `forwarded_headers`). ARCHITECTURE § PII handling
says access is role-gated and audited, "subject to a retention policy (TBD)".
Two things pull in different directions:

- Compliance decisions have to stay defensible for years: who approved what,
  on which evidence, with what score.
- Raw network metadata is the most sensitive data and the least useful over
  time. Its only lasting use is the cross-submission IP/ASN reuse signal.

## [2] Decision

**Retention.** A daily job anonymizes rows in place. It never hard-deletes
them, so foreign keys, scores and audit history stay intact.

| Data | Keep raw for | Then |
|---|---|---|
| Network metadata (`source_ip`, `user_agent`, `forwarded_headers`) | **90 days** after submission | Truncate IP to /24 (IPv4) or /48 (IPv6); keep ASN and country; null out UA and headers |
| Submitted PII on a **reviewed** submission | **5 years** after the review decision | Null the PII fields; keep company name, domain, jurisdiction, score, triage tier, verdict |
| Submitted PII on a submission **never reviewed** | **180 days** after submission | Same anonymization |
| Audit events | **Indefinitely** (append-only) | Never modified; refer to subjects by id and hold no raw PII |
| Adapter cache / raw source responses | Existing per-source TTL | No change |

Config: `ENTITYIQ_RETENTION_NETWORK_DAYS=90`,
`ENTITYIQ_RETENTION_REVIEWED_DAYS=1825`,
`ENTITYIQ_RETENTION_UNREVIEWED_DAYS=180`.

**Access.**

- Operators and leads can read PII in the operator app. Viewing a report
  detail that contains PII records an audit event.
- Only leads can see raw network metadata (full IP, UA, headers). Operators
  see the derived signals: country, ASN, and the reuse flag.
- Integration API keys get reports without raw network metadata and without
  the requester's contact PII. They do get company-level evidence and scores.
- Every anonymization run writes one audit event with the row counts per
  category.

## [3] Consequences

- **Reuse window.** The IP/ASN reuse signal still works after 90 days at /24
  and ASN granularity, but it loses exact-IP matches older than 90 days.
- **Evidence after 5 years.** Reviewed decisions stay explainable from the
  stored score and evidence, but the submitted contact details are gone.
- **Why 5 years.** It matches common AML/KYB record-keeping periods. If a
  deployment's regime requires longer, change the env var; no code change.
- **Migration.** Anonymization needs nullable PII columns. They are already
  nullable, so no destructive migration is needed.

## [4] Conflicts resolved

- **C1 — delete vs anonymize:** anonymize in place, because hard deletes would
  break the append-only audit trail and the report history.
- **C2 — one retention window vs split by data type:** split. Network metadata
  is the riskiest data and the least useful after triage.

## [5] Unchanged and still binding

- No auto-approval. A human decides every approval.
- The audit log stays append-only; retention never touches it.
