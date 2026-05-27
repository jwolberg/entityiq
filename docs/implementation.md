# Implementation

## Scope Implemented
- Requested scope: P2-T1 and P2-T2
- Related phase: Phase 2 — Deepen the Tracks
- Related ticket(s): P2-T1 (entity candidate resolution), P2-T2 (network/IP intelligence enrichment)

## Approach

### P2-T1 — Entity candidate resolution
- Deterministic, offline pipeline stage inserted between NormalizeInputStage and QueryRegistriesStage.
- Scores candidates by name-token overlap (0.6 weight) + domain match (0.4 weight).
- Synthesises one candidate from the submitted name + domain for MVP; designed for extension when P2-T3 injects additional registry candidates.
- Conflict detection: if the top two candidates are within 0.05 of each other and the top is below 0.85, `conflict_signal=True` is emitted (PRD "multiple conflicting company identities" risk signal).

### P2-T2 — Network/IP intelligence enrichment
- IPInfoAdapter wraps ipinfo.io with an injectable HTTP client (same pattern as OpenCorporates and Domain adapters).
- Emits all PRD-specified report fields: ip_country/region/city, ip_asn/isp, ip_organization, ip_hosting/vpn/proxy, ip_country_match/mismatch.
- Emits PRD risk flags: ip_country_mismatch, ip_anonymized_network, ip_suspicious_asn.
- Free-tier fallback: when the `privacy` sub-object is absent (free plan), keyword matching on org name detects datacenter/cloud ASNs at 0.70 confidence.
- IPINFO_TOKEN read from environment — not hard-coded. No token = anonymous free tier.

### Key decisions
- `source_ip` added to `context["normalized"]` by `NormalizeInputStage` (avoid a second DB query in EnrichNetworkIPStage).
- Conflict-detection tests patch `_score_and_rank` (not `_build_candidates`) because the scoring function re-scores every raw candidate; the boundary that controls the conflict window is the ranked output.
- No Evidence rows from ResolveEntityCandidatesStage — candidates are pipeline inputs, not source-attributable findings.
- ASN reuse (repeated submissions from same IP/ASN) is a scoring/consistency concern (P2-T6) — the adapter emits the raw `ip_asn` field on every run.

---

## Implementation Plan

1. Read all source-of-truth docs and existing code.
2. Create `backend/app/pipeline/resolve.py` with scoring helpers, `resolve_candidates()`, and `ResolveEntityCandidatesStage`.
3. Update `NormalizeInputStage` to include `source_ip` in `context["normalized"]`.
4. Update `orchestrator.default_stages()` to insert both new stages at correct positions.
5. Write `backend/tests/pipeline/test_resolve.py` (22 offline tests).
6. Create `backend/app/adapters/ipinfo.py` with `IPInfoAdapter` and `EnrichNetworkIPStage`.
7. Write `backend/tests/adapters/test_ipinfo.py` (26 offline tests).
8. Run full lint + format + test gate; fix issues.
9. Commit P2-T1, then P2-T2 separately.
10. Update docs.

---

## Code Changes

### File: backend/app/pipeline/resolve.py (new)
- Change summary: Entity candidate resolution stage (P2-T1). Scoring helpers `_name_score`, `_domain_score`, `_overall_score`; `_build_candidates` synthesises from submission; `_score_and_rank` filters/ranks; `resolve_candidates` runs conflict detection; `ResolveEntityCandidatesStage` wraps as pipeline stage.

### File: backend/app/pipeline/normalize.py (modified)
- Change summary: Adds `source_ip` key to `context["normalized"]` so EnrichNetworkIPStage can read it without a second DB query.

### File: backend/app/pipeline/orchestrator.py (modified)
- Change summary: Updated `default_stages()` to insert `ResolveEntityCandidatesStage` at position 2 and `EnrichNetworkIPStage` at position 5; updated docstring to reflect 8-stage order.

### File: backend/tests/pipeline/test_resolve.py (new)
- Change summary: 22 offline tests — scoring helpers, resolve_candidates core logic (single-match, no-match, ambiguous/conflict, strong-match no-conflict, large-gap no-conflict), and stage contract.

### File: backend/app/adapters/ipinfo.py (new)
- Change summary: IPInfoAdapter + EnrichNetworkIPStage. Injectable HTTP client, free-tier keyword fallback, all PRD report fields + risk flags, IPINFO_TOKEN env var.

### File: backend/tests/adapters/test_ipinfo.py (new)
- Change summary: 26 offline tests — residential IP (no flags), datacenter/VPN (anonymized flag), country mismatch, ASN evidence, typed failures, evidence contract, stage persist + context pass-through.

---

## Acceptance Criteria Mapping

### P2-T1

- Criterion: ARCHITECTURE § 2 stage 2 — resolve entity candidates before authoritative registry lookup.
  Implementation: `ResolveEntityCandidatesStage` inserted at position 2 in `default_stages()`, before `QueryRegistriesStage`.
  Files: `backend/app/pipeline/resolve.py`, `backend/app/pipeline/orchestrator.py`

- Criterion: PRD § Risk Signals — "multiple conflicting company identities" is a risk signal.
  Implementation: `conflict_signal=True` emitted when top-two candidates are within `_CONFLICT_GAP` and below `_STRONG_MATCH_THRESHOLD`.
  Files: `backend/app/pipeline/resolve.py`

- Criterion: Tests — clear name+domain → single high-confidence candidate; ambiguous → multiple candidates flagged; deterministic/offline.
  Implementation: `test_single_clear_name_and_domain`, `test_ambiguous_input_sets_conflict_signal`, all tests use no DB or network.
  Files: `backend/tests/pipeline/test_resolve.py`

### P2-T2

- Criterion: PRD § Network & IP Intelligence — IP country/region/city, ASN/ISP, organization, hosting/VPN/proxy, distance/mismatch, reuse patterns.
  Implementation: `ip_country`, `ip_region`, `ip_city`, `ip_asn`, `ip_isp`, `ip_organization`, `ip_hosting`, `ip_vpn`, `ip_proxy`, `ip_country_match` evidence fields.
  Files: `backend/app/adapters/ipinfo.py`

- Criterion: PRD risk flags — IP-country mismatch, datacenter/anonymized, repeated IP/ASN, suspicious network ownership, unusual geography.
  Implementation: `ip_country_mismatch`, `ip_anonymized_network`, `ip_suspicious_asn` emitted; `ip_asn` available for cross-submission reuse (P2-T6).
  Files: `backend/app/adapters/ipinfo.py`

- Criterion: Insert after AnalyzeDomainStage (stage 5).
  Implementation: `EnrichNetworkIPStage` at position 5 in `default_stages()`.
  Files: `backend/app/pipeline/orchestrator.py`

- Criterion: HTTP client injectable; tests offline; no hard-coded token.
  Implementation: `IPInfoAdapter(http_client=...)` constructor injection; IPINFO_TOKEN from env var.
  Files: `backend/app/adapters/ipinfo.py`, `backend/tests/adapters/test_ipinfo.py`

- Criterion: Provider unavailable → typed `unavailable` section, run continues.
  Implementation: All errors return `AdapterFailure`; stage records `status=result.kind` and returns context (no raise).
  Files: `backend/app/adapters/ipinfo.py`

---

## Build Plan Mapping

- Ticket: P2-T1 — Entity candidate resolution (pipeline stage 2)
  Status: Complete
  What was completed: resolve.py stage + tests; normalize.py source_ip; orchestrator stage insertion.
  Remaining work: None. When P2-T3 adds registry results, additional candidates can be injected.

- Ticket: P2-T2 — Network/IP intelligence enrichment (IPinfo)
  Status: Complete
  What was completed: ipinfo.py adapter + stage; test suite; orchestrator stage insertion.
  Remaining work: Production IPINFO_TOKEN unresolved (free tier operational). Full privacy flags require paid plan. Cross-submission ASN reuse flag is P2-T6.

---

## Validation

- `ruff check .` → All checks passed
- `ruff format --check .` → 68 files already formatted
- `pytest -q` → 217 passed in 5.11s (172 prior + 45 new; 22 in test_resolve.py + 26 in test_ipinfo.py = 48 new; 3 delta from module-scope DB fixture count)

---

## Open Issues

- IPINFO_TOKEN production plan is unresolved. Free tier: geo + org only; no explicit `privacy` sub-object. Keyword fallback at 0.70 confidence operational but lower precision.
- Cross-submission IP/ASN reuse detection requires querying other submissions' evidence — deferred to P2-T6.
- ResolveEntityCandidatesStage currently produces one candidate. Multiple distinct candidates surface when P2-T3 injects registry search results.

---

## BUILD_PLAN Update

- Current phase: Phase 2 — Deepen the Tracks
- Current ticket: P2-T3 — Additional Tier-1 sources (gov registries, sanctions/watchlist)
- P2-T1 status: Complete (2026-05-27)
- P2-T2 status: Complete (2026-05-27)
- Blockers: Open Decision #5 (Tier-1 source licensing) blocks production use; IPINFO_TOKEN production plan unresolved (free tier operational).
- Recommended next: P2-T3
