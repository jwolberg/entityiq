"""Pipeline stage 1: Normalize Input.

Canonicalizes submitted fields:
  - domain: lowercase, strip scheme/path
  - country: map to ISO 3166-1 alpha-2
  - work_email: lowercase
  - billing_address: strip leading/trailing whitespace
  - tax_id: stub country-aware formatting

Produces a 'normalized' key in the pipeline context that subsequent stages
can consume.  Persists findings as Evidence rows (source='normalize', tier=1).

Implementation note: the normalize stage is implemented in P1-T3.
This module is created as part of P1-T2 so the orchestrator's default_stages()
can import it.  The full implementation is in the P1-T3 commit.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# ISO 3166-1 alpha-2 country name/alias → code mapping (common entries)
# ---------------------------------------------------------------------------

_COUNTRY_MAP: dict[str, str] = {
    # Full names
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
    "germany": "DE",
    "deutschland": "DE",
    "de": "DE",
    "france": "FR",
    "fr": "FR",
    "canada": "CA",
    "ca": "CA",
    "australia": "AU",
    "au": "AU",
    "japan": "JP",
    "jp": "JP",
    "china": "CN",
    "cn": "CN",
    "india": "IN",
    "in": "IN",
    "brazil": "BR",
    "br": "BR",
    "mexico": "MX",
    "mx": "MX",
    "netherlands": "NL",
    "nl": "NL",
    "sweden": "SE",
    "se": "SE",
    "norway": "NO",
    "no": "NO",
    "denmark": "DK",
    "dk": "DK",
    "finland": "FI",
    "fi": "FI",
    "switzerland": "CH",
    "ch": "CH",
    "austria": "AT",
    "at": "AT",
    "belgium": "BE",
    "be": "BE",
    "spain": "ES",
    "es": "ES",
    "italy": "IT",
    "it": "IT",
    "portugal": "PT",
    "pt": "PT",
    "poland": "PL",
    "pl": "PL",
    "singapore": "SG",
    "sg": "SG",
    "new zealand": "NZ",
    "nz": "NZ",
    "south korea": "KR",
    "korea": "KR",
    "kr": "KR",
    "israel": "IL",
    "il": "IL",
    "south africa": "ZA",
    "za": "ZA",
    "nigeria": "NG",
    "ng": "NG",
    "kenya": "KE",
    "ke": "KE",
    "uae": "AE",
    "united arab emirates": "AE",
    "ae": "AE",
    "saudi arabia": "SA",
    "sa": "SA",
    "argentina": "AR",
    "ar": "AR",
    "colombia": "CO",
    "co": "CO",
    "chile": "CL",
    "cl": "CL",
}

# Any 2-letter uppercase string is assumed to already be an ISO code.
_ISO2_RE = re.compile(r"^[A-Z]{2}$")


def normalize_country(raw: str) -> str | None:
    """Map a country name or code to ISO 3166-1 alpha-2.

    Returns None if the country cannot be mapped.
    """
    if not raw:
        return None
    stripped = raw.strip()
    # Already looks like an ISO code — accept as-is.
    if _ISO2_RE.match(stripped):
        return stripped
    looked_up = _COUNTRY_MAP.get(stripped.lower())
    return looked_up  # None if unsupported


def normalize_domain(raw: str) -> str | None:
    """Canonicalize a domain: lowercase, strip scheme and path."""
    if not raw:
        return None
    domain = raw.strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix) :]
    # Strip path, query, fragment
    domain = domain.split("/")[0].split("?")[0].split("#")[0]
    # Strip port
    domain = domain.split(":")[0]
    return domain or None


def normalize_email(raw: str) -> str | None:
    """Lowercase an email address."""
    if not raw:
        return None
    return raw.strip().lower()


def normalize_address(raw: str | None) -> str | None:
    """Strip leading/trailing whitespace from an address string."""
    if not raw:
        return None
    return raw.strip()


def format_tax_id(raw: str | None, country_iso: str | None) -> str | None:
    """Stub country-aware tax-ID formatting.

    Full implementation is deferred (P2 / data-source ticket).  For now this
    returns the stripped value to signal 'no special formatting needed yet'.
    Country-specific formats will be added here as source adapters are built.
    """
    if not raw:
        return None
    return raw.strip()


# ---------------------------------------------------------------------------
# Pipeline stage class
# ---------------------------------------------------------------------------


class NormalizeInputStage:
    """First pipeline stage: normalize submitted fields.

    Reads the Submission linked to the run and produces normalized values
    in the pipeline context dict.  Does NOT raise on malformed input —
    it records None for fields it cannot normalize and continues.
    """

    name = "normalize_input"

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        from app.models.verification_run import VerificationRun  # noqa: PLC0415

        run = db.get(VerificationRun, run_id)
        if run is None:
            raise RuntimeError(f"NormalizeInputStage: run {run_id!r} not found")

        sub = run.submission

        domain = normalize_domain(sub.domain if sub else "")
        country_iso = normalize_country(sub.country if sub else "")
        email = normalize_email(sub.work_email if sub else "")
        address = normalize_address(sub.billing_address if sub else None)
        tax_id = format_tax_id(sub.tax_id if sub else None, country_iso)

        normalized = {
            "domain": domain,
            "country_iso": country_iso,
            "email": email,
            "billing_address": address,
            "tax_id": tax_id,
            "company_name": (
                sub.company_name.strip() if sub and sub.company_name else None
            ),
        }

        return {**context, "normalized": normalized}
