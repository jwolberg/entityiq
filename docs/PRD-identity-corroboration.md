# PRD — External Identity Corroboration: Government/Tax-ID (FEIN) + LinkedIn

**Status:** Draft · **Created:** 2026-05-31 · **Owner:** TBD
**Parent PRD:** [PRD.md](./PRD.md) · **Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md)

> This is a **feature PRD** scoped to two new verification sources. It extends the
> platform described in the parent PRD; it does not restate it. Anything not
> mentioned here keeps the parent PRD's behavior.

---

## 1. Summary

Today EntityIQ corroborates a submitted company against three live source
families: a registry aggregator (OpenCorporates, Tier 1), domain/network
infrastructure (WHOIS/DNS/SSL + IPinfo, Tier 2), and the public web (Tier 3).
Two inputs the onboarding flow already collects are **captured but never
verified against any source**:

- **Tax ID / FEIN** — stored and normalized, shown in the diff, but compared
  against nothing authoritative.
- **LinkedIn URL** — stored and operator-correctable, but never fetched.

This feature adds two new pipeline adapters that close those gaps:

- **Feature A — Government / Tax-ID (FEIN) Verification** (Tier 1): confirm the
  submitted tax identifier maps to a real, active registered entity whose name
  matches the submission.
- **Feature B — LinkedIn Presence Verification** (Tier 3): corroborate that the
  company has a credible, established LinkedIn presence and (where possible) that
  the named requester is plausibly associated with it.

Both flow through the existing evidence → field-comparison → four-layer-scoring
machinery, so the operator UI and report/export API gain new content with
minimal new surface area.

## 2. Why now

- The submitted-vs-discovered diff is the operator's primary trust signal, but
  two of its highest-value rows (tax ID, requester identity) are currently
  "Unverified" by construction.
- **Entity Legitimacy** leans almost entirely on one registry source today;
  an authoritative tax-ID match is among the strongest existence signals.
- **Representation Confidence** (does the requester plausibly represent the
  org?) has the thinnest evidence base of the four layers. LinkedIn is the most
  direct public corroboration for it.

## 3. Goals

### Primary
- Verify the submitted **tax ID/FEIN** against an authoritative source and
  surface a match / mismatch / unverified result in the registration diff.
- Corroborate the company's **LinkedIn presence** and feed it into scoring.
- Strengthen the **Entity Legitimacy** (FEIN) and **Representation Confidence**
  (LinkedIn) layers with attributable evidence and explainable signals.

### Secondary
- Reuse the existing adapter contract, evidence model, and scoring layers — no
  new top-level API resources.
- Keep both sources **optional and gracefully degrading**: when an input is
  absent or a provider is down, the run still completes with that section marked
  `unavailable`.

### Non-Goals
- Not proving organizational **authorization** (consistent with parent PRD).
- Not real-time identity proofing of the individual requester (no KYC/document
  verification).
- Not scraping LinkedIn in violation of its terms (see Risks).
- Not adding new required submission inputs — both features operate on existing
  optional fields.

## 4. Inputs used (all existing)

| Field | Used by | Role |
| --- | --- | --- |
| `tax_id` | Feature A | Primary lookup key for tax-ID verification |
| `country` | Feature A | Selects jurisdiction/provider; FEIN is US-specific |
| `company_name` | A & B | Name-match target for both sources |
| `linkedin_url` | Feature B | Direct handle when supplied (skip search) |
| `requester_full_name` | Feature B | Optional person-association check |
| `domain` | Feature B | Corroborate the LinkedIn page's stated website |

No schema changes to `submission`. Both adapters read from the existing entity
context.

---

## 5. Feature A — Government / Tax-ID (FEIN) Verification

### Behavior
A new **Tier-1 adapter** (`tax_id` / working name `verify_tax_id`) takes the
normalized tax ID + country and queries an authoritative tax/registry source to
confirm: (a) the identifier exists, (b) it maps to an active entity, and (c) the
registered name matches the submitted company name.

US submissions use FEIN/EIN matching; non-US submissions degrade to
`unavailable` for v1 (jurisdiction expansion is a follow-up).

### Provider — OPEN DECISION (blocking)
The IRS does **not** offer a public real-time FEIN lookup. Viable options, to be
resolved before build (mirrors the unresolved OpenCorporates licensing decision):

| Option | Notes |
| --- | --- |
| **IRS TIN Matching (e-Services)** | Authoritative name/TIN match, but gated to filers of information returns; batch-oriented, not a general lookup. |
| **Commercial KYB API** (Middesk, Signzy, Tax1099, etc.) | Real-time name/TIN match + entity status; paid; ToS + cost to clear. **Recommended** for v1. |
| **State Secretary-of-State registries** | Free-ish but fragmented per-state; no unified FEIN index. |

The adapter contract isolates this choice — scoring/UI do not depend on which
provider is selected.

### Evidence emitted (Tier 1, `source = "tax_id"`)
- `tax_id_status` — `verified` / `not_found` / `inactive`
- `tax_id_registered_name` — name on file for the identifier
- `tax_id_name_match` — match confidence vs. submitted `company_name`
- Optional: `tax_id_jurisdiction`, `tax_id_entity_type`

### Field comparison (registration diff)
Adds/upgrades the **Tax ID** row from "Unverified" to `match` / `mismatch` /
`unverified`, plus a derived **Registered Name (per tax ID)** comparison against
the submitted name.

### Scoring contribution → **Entity Legitimacy** layer
- **Trust:** `tax_id_verified_active` (verified + active + name match).
- **Elevated:** `tax_id_not_found`, `tax_id_name_mismatch`,
  `tax_id_inactive_or_dissolved`.
- `unavailable` (no input / provider down) contributes **no** signal and reduces
  layer coverage rather than penalizing the score.

### Failure modes
Typed per the adapter contract (timeout / unavailable / not-found /
rate-limited). `not-found` is a real risk signal; `unavailable` is not.

---

## 6. Feature B — LinkedIn Presence Verification

### Behavior
A new **Tier-3 adapter** (`linkedin` / working name `verify_linkedin`) resolves
the company's LinkedIn Company Page — directly from `linkedin_url` when supplied,
otherwise by name+domain search — and extracts corroborating signals about the
company's presence and (best-effort) the requester's association.

Tier 3 = **fallback enrichment**, consistent with the parent PRD's
authoritative-first principle. LinkedIn never overrides a Tier-1 result; it adds
or withholds confidence.

### Provider — OPEN DECISION
LinkedIn's ToS prohibits unauthorized scraping and there is no open public
company API. Options:

| Option | Notes |
| --- | --- |
| **Official LinkedIn API (Marketing/Company)** | Compliant but requires partner approval; limited fields. |
| **Licensed third-party data provider** | Compliant data access; paid. **Recommended.** |
| **Direct scraping** | **Disallowed** — ToS + brittleness risk; explicitly out of scope. |

### Evidence emitted (Tier 3, `source = "linkedin"`, with attribution URL)
- `linkedin_company_url` — resolved page
- `linkedin_company_name` — name on the page (→ name-match signal)
- `linkedin_employee_count` / `linkedin_followers` — footprint proxies
- `linkedin_founded_year` — corroborates entity age
- `linkedin_website` — cross-check against submitted `domain`
- `linkedin_requester_match` — best-effort: requester name associated with the
  company (only when `requester_full_name` is present)

### Field comparison
- **LinkedIn presence** (found / not found) shown in Contact/Representation.
- **Website match** — LinkedIn-stated site vs. submitted `domain`.
- **Requester ↔ company** association indicator when available.

### Scoring contribution → **Representation Confidence** (primary) + **Entity Legitimacy** (secondary)
- **Trust:** `linkedin_established_presence` (real page + non-trivial footprint +
  website/domain match); `linkedin_requester_associated`.
- **Elevated:** `linkedin_absent_or_thin` (no page or near-zero footprint for a
  company claiming enterprise scale); `linkedin_website_mismatch`;
  `linkedin_recently_created` (maps to the parent PRD's "recently created social
  presence" risk).
- `unavailable` → no signal, reduced coverage.

> **Calibration note:** absence of LinkedIn is weak evidence (many legitimate
> firms have thin pages). It should nudge, not dominate — weight accordingly and
> keep it Tier 3.

---

## 7. Pipeline integration

Insert both into the existing ordered stage list (`pipeline/orchestrator.py`,
`default_stages()`):

- **Feature A** runs in the Tier-1 group, alongside `query_registries` /
  `sanctions_screening` (stage 3, "Query authoritative registries").
- **Feature B** runs in the Tier-3 group, alongside `web_evidence` (stage 6,
  "Crawl/extract public web evidence").

Both honor existing execution properties: per-stage isolation, partial results
with `pending`/`complete`/`unavailable` section status, retries/timeouts,
caching keyed by `(source, lookup_key)`, and re-analysis. The downstream
**consistency** (stage 7) and **risk assessment** (stage 8) stages pick up the
new evidence automatically once they emit the new field comparisons / signals.

## 8. Data model impact

**No new tables.** Both features reuse:
- `evidence` — new `source` values (`tax_id`, `linkedin`) and `field` names.
- `field_comparison` — new rows for tax-ID/name and LinkedIn website/presence.
- `risk_assessment.contributing_signals` — new named signals on existing layers.
- `audit_event` — sources-used logging already covers new adapters.

The only required change is registering the two new source names and their
tier mappings.

## 9. Frontend impact

Reuses the existing detail-panel pattern (`DetailPanels.tsx`,
`RegistrationDiff.tsx`) — no new pages.

- **Registration diff:** Tax ID row becomes a real match/mismatch; add a
  "Registered Name (tax ID)" row.
- **New "Identity Corroboration" panel** (or extend existing panels):
  - Tax-ID verification: status, registered name, name-match badge, source.
  - LinkedIn: company page link, footprint (employees/followers), founded year,
    website-match badge, requester-association indicator, source attribution.
- **Risk Assessment panel:** new trust/elevated signals appear automatically via
  `contributing_signals` (no bespoke UI).
- All three partial states already handled by the panel components
  (`pending` / not-available / data).

## 10. API impact

No new endpoints and no breaking changes. The new evidence, field comparisons,
and signals flow through the existing `GET /reports/{id}`,
`GET /reports/{id}/export`, and the report list shape. Existing API consumers
keep working; new fields are additive within `evidence[]` / `mismatches[]` /
`scores.contributing_signals[]`.

## 11. Auditability

Both adapters are sources used within a run and are recorded as such. No new
operator actions are introduced, so no new audited action types. Re-analysis and
score-change history already capture the effect of the new signals.

## 12. Acceptance criteria

**Feature A — Tax-ID/FEIN**
- [ ] A US submission with a valid FEIN matching the company name produces a
      `tax_id` Tier-1 evidence row and a **match** on the Tax ID diff row.
- [ ] A FEIN that does not resolve produces a `not_found` result and an
      `elevated` Entity-Legitimacy signal.
- [ ] A FEIN that resolves to a different name produces a name **mismatch** and
      an `elevated` signal.
- [ ] No `tax_id` submitted, or provider down → section `unavailable`, run still
      completes, **no** score penalty.
- [ ] Non-US country → `unavailable` (documented v1 limitation).

**Feature B — LinkedIn**
- [ ] A submission with a valid `linkedin_url` resolves the page and emits
      footprint + website-match evidence with source attribution.
- [ ] Missing `linkedin_url` but resolvable by name+domain still produces a
      result; unresolvable → `unavailable` (no penalty).
- [ ] A thin/absent presence for an enterprise-scale claim produces an
      `elevated` Representation signal; an established presence produces a trust
      signal.
- [ ] LinkedIn never overrides a Tier-1 verdict (verified via a case where they
      disagree).

**Cross-cutting**
- [ ] Both sections render correctly in `pending`, `unavailable`, and populated
      states in the operator UI.
- [ ] New evidence/signals appear in `/reports/{id}/export` without breaking
      existing consumers.
- [ ] Backend lint + tests pass; new adapter unit tests cover each typed failure
      mode; consistency/scoring tests cover the new signals.

## 13. Risks & open decisions

1. **Provider selection (both features) — BLOCKING.** No free authoritative
   FEIN lookup; no compliant free LinkedIn company API. Resolve build-vs-buy and
   licensing/cost before implementation (see §5, §6). Same class of unresolved
   decision as OpenCorporates licensing.
2. **LinkedIn ToS / legal.** Direct scraping is out of scope; only official API
   or licensed data. Confirm with legal before integrating.
3. **PII.** Requester-association data is personal data; gate behind the
   existing role-based access + audit + retention policy (parent PRD §PII).
4. **False positives.** Absence of LinkedIn and TIN-match edge cases (recently
   issued FEINs, name variants/DBAs) must be weighted conservatively to avoid
   over-flagging legitimate companies.
5. **Cost/latency.** Paid lookups add per-run cost; rely on the existing
   `(source, lookup_key)` cache and the <2h analysis budget — neither is on a
   latency-critical path.

## 14. Phasing (suggested)

- **Phase 1:** Adapter contract + scoring/diff wiring behind a stubbed provider
  (deterministic fixtures) — proves end-to-end UI/report integration with no
  vendor dependency.
- **Phase 2:** Wire the selected FEIN provider (US-only).
- **Phase 3:** Wire the selected LinkedIn provider.
- **Phase 4 (later):** Non-US tax-ID jurisdictions; requester-association depth.

## 15. Out of scope

- Non-US tax-ID verification (v1).
- Individual identity proofing / document KYC.
- Any LinkedIn data beyond company-page corroboration + best-effort requester
  association.
- New operator actions or API resources.
