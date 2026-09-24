---
title: A new evidence source can silently shift existing signals through tier-wide filters
date: 2026-09-24
tags: [scoring, adapters, identity-corroboration]
anchor: LRN-new-source-tier-filters
---

## [1] Context

Adding the tax-ID (FEIN) source in IC1-T1 (backlog 0007). Its evidence is Tier 1.

## [2] Finding

`entity_legitimacy_signals` built its registry checks from *every* Tier-1 row
(`e.tier == 1`). The new tax-ID rows therefore counted as registry evidence:
they suppressed `no_registry_evidence` and could trigger
`registry_name_unconfirmed` with a tax-ID evidence id, even though no registry
had answered. Separately, the early `return` on the no-registry path skipped
any signals appended later in the function, so the new tax-ID signals
vanished exactly when OpenCorporates was down, which is the common case
without a token.

## [3] Implication

When adding a source, grep scoring for tier-wide filters (`e.tier ==`,
`tier in`) and early returns in the layer functions, and add a test proving
the existing signals are unchanged by the new rows. Filter by `source` or by
field name, not by tier alone.

## [4] References

- backend/app/scoring/signals.py (`entity_legitimacy_signals`, `_tax_id_signals`)
- backend/tests/scoring/test_tax_id_signals.py
  (`test_registry_signals_unchanged_when_tax_id_rows_are_added`,
  `test_tax_id_signals_still_fire_when_registry_evidence_is_missing`)
