"""Pipeline stage 7: Consistency checks (P1-T6).

Compares submitted field values against discovered evidence, producing
FieldComparison rows with match_status: "match" | "mismatch" | "unverified".

Logic per field:
  - If no evidence for the field exists → "unverified"
  - If at least one evidence row matches the submitted value → "match"
  - If evidence exists but no row matches → "mismatch"

Match semantics (case-insensitive, normalised whitespace):
  - company_name: contains or exact match
  - country / jurisdiction: ISO code comparison
  - address (billing_address / legal_address): normalised substring match
  - Registration fields: exact normalised string match

Evidence is gathered from the DB for the current run_id.  The supporting
evidence_id is set to the first matching evidence row for "match" status;
for "mismatch" it is the first relevant evidence row (the discovered value
we compared against).

Fields checked (keys must exist in context["normalized"]):
  - company_name
  - country_iso   (compared against evidence field "jurisdiction")
  - billing_address (compared against evidence field "legal_address")
  - domain        (compared against evidence field "domain_registrar" etc.)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalise helpers
# ---------------------------------------------------------------------------


def _norm(value: str | None) -> str:
    """Lowercase and collapse whitespace."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip().lower())


def _names_match(submitted: str | None, discovered: str | None) -> bool:
    """Case-insensitive: submitted is a substring of discovered or vice versa."""
    s = _norm(submitted)
    d = _norm(discovered)
    if not s or not d:
        return False
    return s in d or d in s


def _iso_match(submitted: str | None, discovered: str | None) -> bool:
    """ISO code comparison: match if equal after uppercasing.

    Also handles jurisdiction_code like "us_ca" — we compare the prefix.
    """
    s = _norm(submitted).upper()
    d = _norm(discovered).upper()
    if not s or not d:
        return False
    # jurisdiction_code can be "US_CA" — check if either is a prefix of the other.
    return s == d or d.startswith(s) or s.startswith(d)


def _address_match(submitted: str | None, discovered: str | None) -> bool:
    """Loose address match: significant tokens overlap."""
    s = _norm(submitted)
    d = _norm(discovered)
    if not s or not d:
        return False
    s_tokens = set(s.split())
    d_tokens = set(d.split())
    # Remove noise words
    noise = {"the", "of", "and", "st", "street", "ave", "avenue", "rd", "road", "blvd"}
    s_tokens -= noise
    d_tokens -= noise
    if not s_tokens or not d_tokens:
        return s in d or d in s
    overlap = s_tokens & d_tokens
    # Require at least 40% token overlap relative to the smaller set.
    smaller = min(len(s_tokens), len(d_tokens))
    return len(overlap) / smaller >= 0.4


# ---------------------------------------------------------------------------
# Field specs: (submitted_field, evidence_field, match_fn)
# ---------------------------------------------------------------------------


def _tax_id_resolves(submitted: str | None, discovered: str | None) -> bool:
    """The FEIN is on file (active or not) with the tax-ID provider (IC1-T2)."""
    return bool(submitted) and _norm(discovered) in ("verified", "inactive")


def _registered_name_match(submitted: str | None, discovered: str | None) -> bool:
    from app.adapters.tax_id import names_match  # noqa: PLC0415

    return names_match(submitted, discovered)


def _linkedin_website_match(submitted: str | None, discovered: str | None) -> bool:
    """LinkedIn-stated website vs. submitted domain; subdomains count (IC1-T5)."""
    from app.adapters.linkedin import bare_domain  # noqa: PLC0415

    s, d = bare_domain(submitted), bare_domain(discovered)
    if not s or not d:
        return False
    return s == d or d.endswith("." + s) or s.endswith("." + d)


_FIELD_SPECS = [
    # (submitted key in context["normalized"], evidence field name, match
    #  function[, comparison field_name when it differs from the submitted key])
    ("company_name", "company_name", _names_match),
    ("country_iso", "jurisdiction", _iso_match),
    ("billing_address", "legal_address", _address_match),
    # Tax ID (IC1-T2): does the FEIN resolve, and to the submitted name?
    ("tax_id", "tax_id_status", _tax_id_resolves),
    (
        "company_name",
        "tax_id_registered_name",
        _registered_name_match,
        "tax_id_registered_name",
    ),
    # LinkedIn (IC1-T5): does the page's stated website match the domain?
    ("domain", "linkedin_website", _linkedin_website_match, "linkedin_website"),
]


# ---------------------------------------------------------------------------
# Core comparison logic
# ---------------------------------------------------------------------------


def _run_comparisons(run_id: str, db: "Session", normalized: dict) -> None:
    """Compute and persist FieldComparison rows for a run.

    For each spec in _FIELD_SPECS:
      1. Look up the submitted value from context["normalized"].
      2. Query Evidence rows for this run + evidence_field.
      3. Compare:
         - No evidence → unverified
         - Any evidence matches submitted value → match
           (first match row as supporting evidence)
         - Evidence exists but none match → mismatch (first evidence as reference)
      4. Persist a FieldComparison row.
    """
    from app.models.evidence import Evidence  # noqa: PLC0415
    from app.models.field_comparison import FieldComparison  # noqa: PLC0415

    for submitted_key, evidence_field, match_fn, *override in _FIELD_SPECS:
        field_name = override[0] if override else submitted_key
        submitted_value = normalized.get(submitted_key)

        # Query evidence for this field from this run.
        evidence_rows = (
            db.query(Evidence)
            .filter(
                Evidence.verification_run_id == run_id,
                Evidence.field == evidence_field,
            )
            .all()
        )

        if not evidence_rows:
            # No discovered data → unverified
            fc = FieldComparison(
                verification_run_id=run_id,
                evidence_id=None,
                field_name=field_name,
                submitted_value=submitted_value,
                discovered_value=None,
                match_status="unverified",
            )
        else:
            # Check if any evidence row matches the submitted value.
            matching_ev = None
            for ev in evidence_rows:
                if match_fn(submitted_value, ev.normalized_value or ev.raw_value):
                    matching_ev = ev
                    break

            if matching_ev is not None:
                fc = FieldComparison(
                    verification_run_id=run_id,
                    evidence_id=matching_ev.id,
                    field_name=field_name,
                    submitted_value=submitted_value,
                    discovered_value=(
                        matching_ev.normalized_value or matching_ev.raw_value
                    ),
                    match_status="match",
                )
            else:
                # Evidence exists but doesn't match → mismatch
                first_ev = evidence_rows[0]
                fc = FieldComparison(
                    verification_run_id=run_id,
                    evidence_id=first_ev.id,
                    field_name=field_name,
                    submitted_value=submitted_value,
                    discovered_value=first_ev.normalized_value or first_ev.raw_value,
                    match_status="mismatch",
                )

        db.add(fc)

    db.commit()


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------


class ConsistencyChecksStage:
    """Pipeline stage: submitted-vs-discovered consistency checks (stage 7).

    Reads evidence already persisted by prior stages (query_registries,
    analyze_domain) and produces FieldComparison rows.

    Writes a summary to context["consistency"].
    """

    name = "consistency_checks"

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        normalized = context.get("normalized", {})

        try:
            _run_comparisons(run_id, db, normalized)
        except Exception as exc:
            # Non-fatal: log and record, but don't fail the stage.
            logger.warning("ConsistencyChecksStage error: %s", exc)
            return {**context, "consistency": {"status": "error", "message": str(exc)}}

        from app.models.field_comparison import FieldComparison  # noqa: PLC0415

        count = (
            db.query(FieldComparison)
            .filter(FieldComparison.verification_run_id == run_id)
            .count()
        )
        return {
            **context,
            "consistency": {"status": "complete", "comparisons": count},
        }
