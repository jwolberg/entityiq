# Implementation

## Scope Implemented
- Requested scope: P2-T3, P2-T4, P2-T5
- Related phase: Phase 2 — Deepen the Tracks
- Related ticket(s): P2-T3, P2-T4, P2-T5

## Approach

### P2-T3 — Sanctions/Watchlist Screening (Tier-1)
- Implemented OFAC SDN list screening in `backend/app/adapters/sanctions.py`.
- Injectable `SdnListFetcher` protocol; tests use a small deterministic CSV fixture.
- Name matching: exact after lowercase normalization + legal-suffix stripping (Ltd, Inc, LLC, Corp, GmbH, etc.).
- On hit: emits `sanctions_hit` + `sanctions_risk_flag` evidence (confidence 0.95, tier=1).
- On clean: emits `sanctions_screened` evidence (confidence 1.0).
- On unavailable: `AdapterFailure(kind="unavailable")` — run continues.
- `SanctionsScreeningStage` registered after `query_registries` in `default_stages()`.
- **Government registries deferred** — Open Decision #5 (per-country licensing/variance) unresolved.

### P2-T4 — Public Web Evidence (Tier-3)
- Implemented in `backend/app/adapters/web.py`.
- `WebAdapter` fetches the company domain (httpx); extracts brand, emails, phones, addresses, footprint.
- `_PlaywrightFetcher` exists but is lazy-imported inside `__init__` only — never at module level.
- Tests inject `FakeWebFetcher` (returns canned HTML); no network, no browser in tests.
- Evidence: `web_brand`, `web_contacts_email`, `web_contacts_phone`, `web_contacts_address`, `web_employee_footprint` / `web_thin_footprint`. All tier=3 with source attribution.
- `WebEvidenceStage` registered after `enrich_network_ip`, before `consistency_checks`.

### P2-T5 — Adapter Robustness
- `backend/app/adapters/cache.py`: `AdapterCache` (thread-safe, per-source TTL, FIFO eviction at max_size). `CachedAdapter` wrapper is transparent to callers.
- `backend/app/adapters/ratelimit.py`: `RateLimiter` (token bucket per source, jittered exponential backoff). `RateLimitedAdapter` wrapper. `SourceAvailabilityTracker` records available/unavailable per source per run.
- No Redis dependency — in-process stdlib only.

### Key decisions
- Gov registries are deferred per Open Decision #5 (documented in implementation-notes).
- Playwright is lazy-imported inside `_PlaywrightFetcher.__init__` only; `playwright` is not added to pyproject.toml since tests use fakes.
- Cache key defaults to `company_name|domain` (lowercased). Custom key functions are injectable.
- Rate limiter blocks with jittered backoff up to `max_wait_seconds`, then returns `AdapterFailure(rate_limited)`.

---

## Implementation Plan

1. Read base adapter + orchestrator + existing adapter patterns.
2. P2-T3: Write `sanctions.py` with injectable fetcher + `SanctionsScreeningStage`. Update orchestrator. Write 25 tests.
3. P2-T4: Write `web.py` with httpx fetcher, lazy Playwright, HTML extractors, `WebEvidenceStage`. Write 29 tests including playwright isolation check.
4. P2-T5: Write `cache.py` (AdapterCache + CachedAdapter) and `ratelimit.py` (RateLimiter + RateLimitedAdapter + SourceAvailabilityTracker). Write 31 tests.
5. Run full gate before each commit.

---

## Code Changes

### File: backend/app/adapters/sanctions.py
- New file: OFAC SDN adapter with injectable fetcher, name normalization, evidence emission.
- `SanctionsScreeningStage` pipeline wrapper.

### File: backend/app/adapters/web.py
- New file: Tier-3 web evidence adapter. `WebAdapter` + `WebEvidenceStage`. Playwright lazy-imported only inside `_PlaywrightFetcher.__init__`.

### File: backend/app/adapters/cache.py
- New file: `AdapterCache` (thread-safe, per-source TTL, FIFO eviction). `CachedAdapter` wrapper.

### File: backend/app/adapters/ratelimit.py
- New file: `RateLimiter` (token bucket + backoff). `RateLimitedAdapter` wrapper. `SourceAvailabilityTracker`.

### File: backend/app/pipeline/orchestrator.py
- Updated `default_stages()` to include `SanctionsScreeningStage` (after `query_registries`) and `WebEvidenceStage` (after `enrich_network_ip`).

### File: backend/tests/adapters/test_sanctions.py
- New file: 25 tests for SanctionsAdapter and SanctionsScreeningStage.

### File: backend/tests/adapters/test_web.py
- New file: 29 tests for WebAdapter and WebEvidenceStage (including Playwright isolation check).

### File: backend/tests/adapters/test_cache.py
- New file: cache hit/miss/TTL/eviction/capacity tests + CachedAdapter.

### File: backend/tests/adapters/test_ratelimit.py
- New file: rate limiter backoff/exhaustion, RateLimitedAdapter, SourceAvailabilityTracker tests.

### File: docs/implementation-notes.md
- Appended entries for P2-T3, P2-T4, P2-T5.

### File: docs/BUILD_PLAN.md
- Updated P2-T3/T4/T5 statuses to Complete; current ticket updated to P2-T6.

---

## Acceptance Criteria Mapping

- **PRD § Tier 1 (sanctions/registries)**: SanctionsAdapter screens against OFAC SDN. Gov registries deferred (Open Decision #5).
- **PRD § Tier 3 (public web)**: WebAdapter fetches domain, extracts contacts + branding + footprint.
- **PRD § FE § Contact Information**: web_contacts_email / phone / address emitted with attribution.
- **ARCHITECTURE § 4 (adapter contract)**: All adapters return typed AdapterResult; never raise. CachedAdapter + RateLimitedAdapter are transparent wrappers. SourceAvailabilityTracker records coverage.
- **ARCHITECTURE § 4 (caching)**: AdapterCache keyed by (source, lookup_key) with per-source TTL.
- **ARCHITECTURE § 4 (rate limiting)**: RateLimiter token bucket with backoff per source.
- **ARCHITECTURE § 4 (graceful degradation)**: Unavailable sources yield AdapterFailure, not failed runs.

---

## Build Plan Mapping

- **P2-T3**: Complete (2026-05-27) — OFAC SDN sanctions screening; gov registries deferred.
- **P2-T4**: Complete (2026-05-27) — Web evidence adapter + 29 offline tests.
- **P2-T5**: Complete (2026-05-27) — Cache + rate limiter + availability tracker + 31 offline tests.

---

## Validation

- `.venv/bin/ruff check .` — All checks passed
- `.venv/bin/ruff format --check .` — 76 files already formatted
- `.venv/bin/pytest -q` — **302 passed** (217 existing + 25 P2-T3 + 29 P2-T4 + 31 P2-T5)
- No browser installed; `playwright` not imported at module level (verified by `test_playwright_not_imported_at_module_level`).

---

## Open Issues

- **Gov registries (P2-T3 partial)**: Open Decision #5 blocks per-country registry adapters. Deferred explicitly.
- **Playwright as optional dep**: Not added to pyproject.toml. A future ticket should add `playwright` as an optional dep and document the `playwright install chromium` step for production.
- **Cache not applied to existing adapters in default_stages()**: The wrappers exist but are not yet wired into `default_stages()`. Wiring them requires the P2-T6 scoring integration (which reads `SourceAvailabilityTracker`) to be in place first.
- **IPINFO_TOKEN production plan**: Unresolved (same class as Open Decision #5). Free tier operational.

---

## BUILD_PLAN Update

- Current phase: Phase 2 — Deepen the Tracks
- Current ticket: P2-T6 — Full four-layer scoring + signal catalog
- P2-T3 status: Complete
- P2-T4 status: Complete
- P2-T5 status: Complete
- Recommended next: P2-T6
