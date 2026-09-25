---
id: 0003
title: Libraries for subject encryption and person-name matching
anchor: ADR-0003
status: accepted
date: 2026-09-24
supersedes:
superseded-by:
---

Settles backlog ticket 0028 (IS0-T2, individual screening). These are the only
new runtime dependencies in the screening plan; the owner approved them
2026-09-24.

## [1] Context

Individual screening needs two capabilities the stack doesn't have:

- **Per-subject envelope encryption** for subject PII and frozen decision
  inputs, so they can be crypto-shredded after the AML retention period (PRD-IDV
  C5; ADR-0004 will record the retention policy).
- **Transliteration and phonetic keys** so blocking survives script changes,
  diacritics and spelling variants (PRD-IDV F6). Blocking recall is the release
  gate (PRD-IDV §[7]).

Both must be deterministic for a pinned version (PRD-IDV N1): the same input
must give the same keys, forever, or replay breaks.

## [2] Decision

| Need | Library | Version | License | Why |
|---|---|---|---|---|
| Envelope encryption (AES-256-GCM data keys, wrapped by a master key) | `cryptography` (PyCA) | 50.0.1 | Apache-2.0 OR BSD-3-Clause | The de-facto standard; audited primitives; wheels for macOS and Linux; `AESGCM` is all we need |
| Transliteration to ASCII | `anyascii` | 0.3.3 | ISC | Pure Python, no native deps, covers all scripts; output is fixed per version |
| Phonetic keys | `jellyfish` | 1.2.1 | MIT | Metaphone + NYSIIS (and Jaro-Winkler for scoring) in one well-used package with wheels |

Pinned exactly in `backend/pyproject.toml`. Versions were checked on PyPI
2026-09-24; all releases are older than 3 days.

**Rejected:**
- **PyICU**: the most thorough transliteration, but it needs the native ICU
  library on every machine and in CI.
- **`unidecode`**: GPL-2.0, which is incompatible with the project's licensing
  stance.
- **Hand-rolled AES**: never.

## [3] Consequences

- **Determinism depends on the pins.** Upgrading `anyascii` or `jellyfish`
  can change blocking keys. Upgrades must re-run the corpus recall gate and
  replay check, and are recorded as a new rule/normalizer version.
- **Known transliteration gaps to design around (verified 2026-09-24):**
  - Arabic transliterates without vowels ("محمد علي" → "mhmd `ly"), although
    its Metaphone key still matches "Mohammed" (MHMT).
  - Chinese output is concatenated ("XiJinPing").
  - "Müller" → "Muller" but "Mueller" stays as is; their Metaphone keys match
    (MLR).

  Blocking therefore uses several keys per name (normalized tokens, phonetic
  keys, consonant skeletons, and CamelCase splitting for CJK output) instead
  of one transliterated string.
- **The master key is an env secret** (`ENTITYIQ_SCREENING_MASTER_KEY`). Losing
  it makes all subject PII unrecoverable. That's the point of crypto-shredding,
  and also an operational risk to document in RUNBOOK.

## [4] Unchanged and still binding

- No model/LLM in v1 (PRD-IDV C3). These are deterministic libraries only.
