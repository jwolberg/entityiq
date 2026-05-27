"""Pipeline stage 2: Resolve entity candidates (P2-T1).

Matches the normalised submission against candidate real-world entities
BEFORE the authoritative-registry lookup so the registry stage can
disambiguate which record it found.

Algorithm
---------
1. From context["normalized"] derive a *name key* (lowercase, collapsed
   whitespace) and a *domain key* (public-suffix root, e.g. "acme.com").
2. Score each candidate on two axes:
   - name_score  in [0.0, 1.0] — token overlap between submitted name and
                                  candidate name
   - domain_score in [0.0, 1.0] — exact domain match (1.0), TLD-stripped match
                                  (0.8), or no match (0.0)
3. overall_score = 0.6 * name_score + 0.4 * domain_score
4. Candidates with overall_score >= 0.4 are included in the ranked set.
5. If the top-two candidates are within 0.05 of each other AND overall_score
   is below 0.85, they are considered *conflicting identities* → a risk signal
   is emitted (PRD § Risk Signals: "multiple conflicting company identities").

In the MVP the only source of candidates is the normalised submission itself
(we synthesise one candidate from name + domain).  When P2-T3 adds additional
Tier-1 sources, those sources can inject additional candidate records into
context["candidates"] before this stage refines them.  The contract is designed
for that extension.

Output written to context
-------------------------
context["candidates"] = {
    "status":           "single_match" | "ambiguous" | "no_match",
    "candidates":       [...],  # ranked list of CandidateEntity dicts
    "conflict_signal":  True | False,   # PRD risk flag
    "top_candidate":    {...} | None,   # highest-scoring candidate (if any)
}

Each CandidateEntity dict:
{
    "name":          str,    # candidate company name
    "domain":        str | None,
    "name_score":    float,
    "domain_score":  float,
    "overall_score": float,
}

Design notes
------------
- Does NOT persist Evidence rows directly (candidates are inputs to the
  registry stage, not conclusions from a source).  The conflict_signal is
  a pipeline flag; evidence for risk scoring is produced by P2-T6.
- Fully deterministic: no network I/O, no external state.
- 0.4 inclusion threshold and 0.05 conflict window are module-level constants
  so tests can validate edge cases without monkey-patching.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum overall_score for a candidate to be included in the result set.
_INCLUDE_THRESHOLD = 0.4

# If the top-two candidates are within this gap AND both are below the strong-
# match threshold, they are flagged as conflicting.
_CONFLICT_GAP = 0.05

# If the best candidate scores at or above this threshold it is treated as a
# clear single match (not conflicting even if a second candidate is close).
_STRONG_MATCH_THRESHOLD = 0.85

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_name(name: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not name:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _name_tokens(name: str) -> set[str]:
    """Split a normalised name into non-trivial tokens."""
    _STOP = {
        "inc",
        "ltd",
        "llc",
        "corp",
        "co",
        "the",
        "and",
        "of",
        "a",
        "an",
        "company",
        "group",
        "holdings",
        "gmbh",
        "ag",
        "plc",
        "bv",
        "sas",
        "sa",
        "sl",
    }
    tokens = set(_normalize_name(name).split())
    return tokens - _STOP or tokens  # keep all if stop-words ate everything


def _name_score(submitted: str | None, candidate: str | None) -> float:
    """Token-overlap score between two company names.

    Returns a value in [0.0, 1.0].  Uses Jaccard-like overlap relative to
    the *smaller* token set so that short names can fully match long names.
    """
    s_tokens = _name_tokens(submitted)
    c_tokens = _name_tokens(candidate)
    if not s_tokens or not c_tokens:
        return 0.0
    overlap = s_tokens & c_tokens
    smaller = min(len(s_tokens), len(c_tokens))
    return len(overlap) / smaller


def _strip_www(domain: str | None) -> str:
    """Remove leading 'www.' prefix if present."""
    if not domain:
        return ""
    d = domain.lower().strip()
    return d[4:] if d.startswith("www.") else d


def _domain_score(submitted: str | None, candidate: str | None) -> float:
    """Domain match score in [0.0, 1.0].

    1.0 — exact match after www-stripping.
    0.8 — same registered domain (strip first label for sub-domain cases).
    0.0 — no meaningful match.
    """
    s = _strip_www(submitted)
    c = _strip_www(candidate)
    if not s or not c:
        return 0.0
    if s == c:
        return 1.0

    # Compare the last two labels (registered domain) to catch sub-domain variants.
    def _root(d: str) -> str:
        parts = d.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else d

    if _root(s) == _root(c):
        return 0.8
    return 0.0


def _overall_score(ns: float, ds: float) -> float:
    return round(0.6 * ns + 0.4 * ds, 4)


# ---------------------------------------------------------------------------
# Candidate building
# ---------------------------------------------------------------------------


def _build_candidates(normalized: dict) -> list[dict]:
    """Build a ranked list of candidate entities from the normalised context.

    Currently synthesises a single candidate from the submitted name + domain.
    Additional candidates (from registry search results, future sources) can
    be injected by upstream stages via context["candidate_hints"].
    """
    submitted_name = normalized.get("company_name")
    submitted_domain = normalized.get("domain")

    # Build the primary candidate from the submission itself.
    candidates: list[dict] = []

    if submitted_name or submitted_domain:
        ns = _name_score(submitted_name, submitted_name)  # 1.0 by definition
        ds = _domain_score(submitted_domain, submitted_domain)  # 1.0 by def
        candidates.append(
            {
                "name": submitted_name or "",
                "domain": submitted_domain,
                "name_score": ns,
                "domain_score": ds,
                "overall_score": _overall_score(ns, ds),
            }
        )

    return candidates


def _score_and_rank(
    submitted_name: str | None,
    submitted_domain: str | None,
    raw_candidates: list[dict],
) -> list[dict]:
    """Score a list of raw candidate dicts against the submitted name+domain.

    Each dict must have at minimum "name" and optionally "domain".
    Returns candidates with overall_score >= _INCLUDE_THRESHOLD, sorted
    descending by overall_score.
    """
    scored: list[dict] = []
    for cand in raw_candidates:
        ns = _name_score(submitted_name, cand.get("name"))
        ds = _domain_score(submitted_domain, cand.get("domain"))
        overall = _overall_score(ns, ds)
        if overall >= _INCLUDE_THRESHOLD:
            scored.append(
                {
                    **cand,
                    "name_score": round(ns, 4),
                    "domain_score": round(ds, 4),
                    "overall_score": overall,
                }
            )

    scored.sort(key=lambda c: c["overall_score"], reverse=True)
    return scored


def resolve_candidates(normalized: dict) -> dict:
    """Core resolution logic — deterministic, no I/O.

    Args:
        normalized: The context["normalized"] dict from the normalize stage.

    Returns:
        A dict suitable for context["candidates"].
    """
    submitted_name = normalized.get("company_name")
    submitted_domain = normalized.get("domain")

    # Primary candidate from submission + any injected hints.
    raw_candidates = _build_candidates(normalized)

    ranked = _score_and_rank(submitted_name, submitted_domain, raw_candidates)

    if not ranked:
        return {
            "status": "no_match",
            "candidates": [],
            "conflict_signal": False,
            "top_candidate": None,
        }

    top = ranked[0]

    # Conflict detection: two candidates within _CONFLICT_GAP AND neither
    # is a strong match → multiple conflicting company identities risk signal.
    conflict_signal = False
    if len(ranked) >= 2:
        second = ranked[1]
        gap = top["overall_score"] - second["overall_score"]
        if gap <= _CONFLICT_GAP and top["overall_score"] < _STRONG_MATCH_THRESHOLD:
            conflict_signal = True

    status = "single_match" if len(ranked) == 1 else "ambiguous"

    return {
        "status": status,
        "candidates": ranked,
        "conflict_signal": conflict_signal,
        "top_candidate": top,
    }


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------


class ResolveEntityCandidatesStage:
    """Pipeline stage 2: resolve entity candidates.

    Reads context["normalized"] (produced by NormalizeInputStage) and
    writes context["candidates"].

    This stage is deterministic and fully offline — it does not make network
    calls or DB writes.  It prepares the ranked candidate set that the
    subsequent QueryRegistriesStage uses for disambiguation.

    Inserted between NormalizeInputStage (stage 1) and QueryRegistriesStage
    (stage 3) in orchestrator.default_stages().
    """

    name = "resolve_entity_candidates"

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        normalized = context.get("normalized", {})
        candidates_ctx = resolve_candidates(normalized)
        return {**context, "candidates": candidates_ctx}
