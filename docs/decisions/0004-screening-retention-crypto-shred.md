---
id: 0004
title: Screening subject retention by crypto-shredding
anchor: ADR-0004
status: accepted
date: 2026-09-24
supersedes:
superseded-by:
---

Implements PRD-IDV C5 (resolved 2026-09-24) in backlog ticket 0033 (IS1-T5).
Libraries: ADR-0003.

## [1] Context

Screening decisions must be replayable (F16), so they freeze their inputs,
including subject PII, and they're append-only (F14; database-enforced by
0032). AML record-keeping needs those records for about 5 years after the
customer relationship ends, but after that the PII should be gone. We can't
delete or rewrite the rows.

## [2] Decision

- **Envelope encryption per subject.** Each subject gets a random AES-256-GCM
  data key, stored only wrapped under the master key
  (`ENTITYIQ_SCREENING_MASTER_KEY`, base64, 32 bytes). Subject PII and every
  frozen decision bundle for the subject are encrypted with that data key, and
  the subject id is bound in as associated data (blobs can't be swapped
  between subjects).
- **Crypto-shred at the end of retention.** Once `relationship_ended_at` is
  more than `ENTITYIQ_SCREENING_RETENTION_DAYS` (default 1825) in the past,
  `python -m app.screening.retention` destroys the wrapped key and clears the
  subject's PII blob. The job is idempotent and writes one audit event with
  counts only.
- **Decision rows are never touched.** After shredding they still show
  terms, thresholds, list snapshots and dispositions, but the frozen PII is
  unrecoverable.
- **Subjects with no `relationship_ended_at` are never shredded.**

## [3] Consequences

- **Replay after shredding** can no longer reproduce a verdict that needed the
  subject's attributes; it reports the decision as shredded instead. This is
  intended.
- **The master key is critical.** Losing it is equivalent to shredding
  everyone; leaking it exposes every unshredded subject. Keep it in the
  secret manager, never in the repo, and rotate by re-wrapping data keys
  (follow-up; not in v1).
- **`screening_subject_key` is mutable** (shredding NULLs the key) and is not
  append-only by design.

## [4] Unchanged and still binding

- ADR-0002 (KYB PII retention) still governs business-verification
  submissions.
- The audit log and decision tables stay append-only.
