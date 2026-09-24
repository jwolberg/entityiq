# Build vs. PRD Assessment — 2026-09-24

A point-in-time audit of the implementation against [PRD.md](./PRD.md) and
[problem-statement.md](./problem-statement.md). It was done to plan the
project's use as a public portfolio showcase. Resulting work is tracked in
[BUILD_PLAN.md](./BUILD_PLAN.md) Phases 4–5.

## [1] Method

- Read the code for every PRD requirement instead of trusting BUILD_PLAN
  statuses. Two independent reviews covered (a) inputs, sources, signals, and
  scoring and (b) UI, API, audit, performance, and demo readiness. Their key
  claims were then spot-checked by hand.
- Ran every gate: backend `pytest` 385 passed; `ruff check` and `ruff format`
  clean; frontend `vitest` 18 passed; eslint and `tsc` clean.
- **Ran the real pipeline live.** Booted the API on SQLite with Celery in eager
  mode, signed in as a seeded operator, and submitted Stripe, Inc. / stripe.com.
  The run completed in about 12 s. Several findings below come only from this
  run; the offline test suite cannot catch them.

Status key: **Done** means built, wired, and tested. **Partial** means it exists
but is incomplete or not wired in. **Missing** means not built. **Deferred**
means blocked on a recorded open decision.

## [2] Headline

The architecture is complete and sound. That covers the async 10-stage
pipeline with per-stage failure isolation, the evidence model, deterministic
four-layer scoring with triage and explainability, the operator workbench, the
integration API with API-key auth, and the append-only audit log. Every
challenge requirement except the HQ map has a working path.

The gap is **fidelity against real data**, not missing structure:

1. Some scoring signals are never wired to what the adapters actually emit.
2. Two live sources fail silently in a real run.
3. Some things are recorded but never shown (audit trail, review notes).

The live Stripe run shows the combined effect. A clearly legitimate company
scored **42/100, "review"**, with entity score 58. It should land in
`pre_clear`.

## [3] Findings confirmed by the live run

| # | Finding | Evidence | Effect |
|---|---|---|---|
| L1 | **OpenCorporates returns HTTP 401** without an API token. The adapter has no env var for a token. | `curl api.opencorporates.com/v0.4/companies/search` → 401; `adapters/opencorporates.py:110` takes `api_token` only as a constructor arg | Tier-1 registry evidence is never produced. Every company gets `registry_name_unconfirmed`, and the report's `sources` list silently omits the registry. |
| L2 | **WHOIS always unavailable.** The `whois` package is imported lazily but not declared as a dependency. | `adapters/domain.py:89`; stripe.com run emitted `whois_status=unavailable` | No domain age, so the `long_lived_domain` trust signal and the `recently_registered` risk signal never fire. |
| L3 | **`httpx` is a dev-only dependency** but the web, registry, and IP adapters use it at runtime. | `pyproject.toml` `[dev]` extra | A production install (`pip install .`) breaks three adapters. |
| L4 | **Web contact extraction produces junk.** | stripe.com run: email `jane.diaz@example.com` (a placeholder on the page), phone `100000000000`, address `"100 companies have"` | The Contact panel shows nonsense, and `web_contact_email_found` gives trust credit for it. |
| L5 | IP enrichment is skipped for local submissions (127.0.0.1). | Expected behavior | A local demo never shows the network layer unless the demo data supplies realistic IPs. |

## [4] Scoring and pipeline wiring defects

| # | Finding | Evidence |
|---|---|---|
| W1 | **MX/SPF trust signals can never fire.** The adapter emits `mx_records` / `spf_record`; scoring filters on `mx_present` / `spf_present`. | `adapters/domain.py:338,359` vs `scoring/signals.py:267-268` |
| W2 | **No test runs the real `default_stages()` pipeline.** Signal tests build fake evidence with the field names scoring expects, so W1 is invisible. | `grep default_stages backend/tests` → 0 hits |
| W3 | **"Multiple conflicting identities" is computed, then discarded.** `resolve.py` sets `conflict_signal` in the pipeline context; scoring reads only DB rows. | `pipeline/resolve.py:256-276`; `grep conflict_signal app/scoring` → 0 |
| W4 | **Free/disposable email is detected but never scored.** It is only returned in the 202 response. | `api/submissions.py:30-73`; no evidence row, no signal |
| W5 | **`valid_tax_id` trust signal is dead code.** No adapter emits `tax_id` evidence. | `scoring/signals.py:210-222` |
| W6 | **Docstrings claim signals that don't exist**: `suspicious_dns_infrastructure`, `inconsistent_contact_information`, and IPinfo's `ip_distance_flag`. | `scoring/signals.py:25,448`; `adapters/ipinfo.py:12` |
| W7 | The representation layer doesn't assess the requester. `requester_full_name` and `linkedin_url` are stored but unused. | Addressed by the identity-corroboration plan |

## [5] Requirement coverage

### [5.1] Verification sources (PRD § Verification Sources)

| Requirement | Status | Note |
|---|---|---|
| Sanctions / watchlist | Done | OFAC SDN, exact match after legal-suffix stripping; company name only (not requester) |
| OpenCorporates | Partial | Wired and tested offline; **401 live (L1)**; licensing unresolved (Open Decision #5) |
| Government registries, other structured DBs | Deferred | Open Decision #5 |
| WHOIS age, registrar | Partial | Code done; **never runs live (L2)**; registrar captured but not rated |
| MX / SPF / DKIM | Partial | DKIM done; MX/SPF trust signals dead (W1); `no_mx` risk works |
| SSL | Partial | Presence scored; certificate age not |
| DNS anomalies | Missing | Claimed in a docstring (W6) |
| Company website / contacts | Partial | Works; extraction quality poor (L4); Playwright fallback is never auto-used |
| LinkedIn, directories, press, social | Missing | LinkedIn is covered by the identity-corroboration plan |

### [5.2] Risk signals (PRD § Risk Signals)

Done: recently registered domain, no MX, registry mismatch, billing-address
mismatch, thin website / employee footprint, long-lived domain, registry
confirmation, consistent addresses, stable web presence.
Partial: domain-country mismatch (IP country only), matching contact info (trust
credit only, no comparison).
**Not effective:** disposable email (W4), conflicting identities (W3), valid tax
ID (W5), suspicious DNS and inconsistent contacts (W6), recently created social
presence, domain-ownership verification.

### [5.3] Network & IP intelligence

Capture (trusted IP, UA, headers, endpoint): Done. IPinfo geo/ASN/org: Done.
VPN/proxy: Partial (free tier uses org-name keywords). Distance/geography:
Partial (country equality only). Cross-submission reuse: Done (ASN level). The
frontend has no network panel; results appear only as named flags.

### [5.4] Scoring and explainability

Done: 0–100 overall score, the four-layer breakdown, per-signal evidence IDs,
source coverage, and lower confidence (not higher risk) when sources are
missing. The human-only decision is enforced by code and tests. What limits
scoring today is its inputs (sections 3–4), not the engine.

### [5.5] Operator app

| Requirement | Status | Note |
|---|---|---|
| Sign in, audited | Done | Sign-out is client-side only; the server token stays valid. In-memory sessions with no TTL. |
| Dashboard list, date, score, status, filters | Done | Filtering is client-side over the full list |
| Registration diff (match/mismatch) | Done | |
| DNS/domain, registry, contacts panels | Done | Content is limited by L1, L2, L4 |
| **HQ visualization (map + address confidence)** | Missing | P2-T9, blocked on Open Decision #4 (map provider) |
| Risk assessment panel | Partial | Saved operator notes are never shown |
| Mark reviewed | Partial | **The report detail has no review status**, so a reviewed company reopens as unreviewed and a second click hits a 409 shown as an error |
| Re-run, correct + re-run, export | Done | Audited |
| Check New Company form | Done | Added after P2-T12; not in the plan |

### [5.6] API, auth, audit, performance

| Requirement | Status | Note |
|---|---|---|
| Submission endpoint (all fields, idempotency) | Done | |
| Report and export endpoints | Done | **`GET /reports/{run_id}` has no auth check.** Anyone with a run ID can read a full report. |
| Integrating-system API keys | Done | No provisioning endpoint or UI |
| Lead role | Partial | `require_lead` exists and is tested but gates no route |
| Audit log written | Done | Append-only; covers all operator and system actions |
| **Audit log readable** | Missing | No API or UI; P3-T2 |
| < 2 h target | Missing | `started_at`/`finished_at` recorded but never measured; no stage or task timeout; live run took ~12 s |
| Partial results viewable | Done | `section_statuses` shown end to end |
| Postgres, Redis/Celery paths | Partial | Code exists; only SQLite + eager mode has ever been run |

## [6] Showcase readiness

| Item | State |
|---|---|
| README | Said "Pre-implementation"; corrected alongside this assessment |
| One-command run | None. Manual venv + alembic + inline Python snippets to seed an operator and API key. No Docker, no seed script. |
| Demo data | None. A fresh install shows an empty dashboard. |
| CI on GitHub | **None runs.** Only `.gitlab-ci.yml` is committed; untracked `.github/workflows/ci.yml` is an unused bun template. |
| Screenshots / hosted demo | None |
| Git history | Earlier commits still reference the originating company (user decision: current files only) |
| Root `CLAUDE.md` | Unfilled template (untracked) |

## [7] Recommended order

1. **Make scores correct on real data**: W1+W2, L1–L3, L4. Until these are
   fixed, any live demo shows legitimate companies as "review" with junk
   contacts.
2. **Make the demo easy to run and look complete**: seed and demo data, a
   one-command start, GitHub CI, a README with screenshots.
3. **Close visible product gaps**: review state and notes read path, report
   auth, the HQ map (Leaflet + OpenStreetMap needs no API key), and the audit
   view with lead gating.
4. **Wire the dropped signals**: W3–W6.
5. Then the identity-corroboration feature and the rest of Phase 3.

The ticket mapping is in [BUILD_PLAN.md](./BUILD_PLAN.md) § Phase 4 and § Phase 5.
