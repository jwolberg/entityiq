---
id: 0007
title: Retention and visibility of SAM.gov exclusion data (addendum to ADR-0002)
anchor: ADR-0007
status: accepted
date: 2026-10-16
supersedes:
superseded-by:
---

Extends ADR-0002 to the public-records sources added for the KYB pipeline (plan `docs/plans/2026-10-16-001-feat-public-records-sources-plan.md`, KTD12; tickets 0091, 0092, 0095). ADR-0002 covers submitted PII and network metadata. It doesn't cover third-party public lists that name people.

## [1] Context

The SAM.gov public exclusions extract is a full daily list of every active federal exclusion. The live file on 2026-10-16 had 169,025 rows, 133,775 of them Individual. Individual rows carry a person's name and city/state. CourtListener dockets can also name people, for example sole-proprietor debtors. This is public government data, but storing it in bulk and showing it to integrations still needs a stated rule.

## [2] Decision

- **Store every classification.** Individual rows are kept, not filtered at ingest, so later person screening can use them. In v1 they never strong-match a business (plan R6): they can appear only as a possible match and never score.
- **Keep the newest 7 SAM snapshots.** Each ingest prunes older SAM snapshots together with their `sam_exclusion_record` rows (`app/lists/sam.py`, `KEEP_SNAPSHOTS`). Other lists' snapshots are untouched, because screening decisions replay against them (ADR-0004).
- **Redact party names for integration API keys.** Report evidence served to an API-key principal replaces natural-person party names (SAM Individual rows; docket parties that aren't the matched company) with a placeholder. Operators see full names. **Not built yet:** this is planned where the evidence is served, in tickets 0092 and 0095.
- **Evidence rows follow the existing per-source TTL** (ADR-0002 §2). An evidence row copies only the matched record's fields, never the whole list. Evidence must copy the record rather than reference it: a foreign key from evidence (or anything else) to `sam_exclusion_record` or to a SAM `list_snapshot` row would make pruning fail. Ticket 0092 must keep it that way and test it.

## [3] Consequences

- Peak SAM storage is about 7 × 170k rows. Pruning keeps it bounded with no separate job.
- The audit trail stays intact. A decision cites the evidence row, which keeps its own copy of the matched record after the snapshot is pruned.
- Revisit this ADR if person screening starts consuming SAM Individual rows. That use would need its own matching and disposition rules (PRD-IDV).
