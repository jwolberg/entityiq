"""Pipeline stage 1: Normalize Input (P1-T3).

Canonicalizes submitted fields:
  - domain: lowercase, strip scheme/path/port
  - country: map to ISO 3166-1 alpha-2 from name or code
  - work_email: lowercase + strip
  - billing_address: strip leading/trailing whitespace
  - tax_id: stub country-aware formatting (stripped; full formatting deferred)

Produces a 'normalized' key in the pipeline context that subsequent stages
can consume.

Design notes:
  - Does NOT raise on malformed / unrecognised input; records None for fields
    that cannot be normalised.
  - Does NOT persist Evidence rows directly (P1-T3 scope); subsequent stages
    can read context['normalized'] and persist as needed.
  - Country map covers ISO-common aliases; additions are welcome as new locales
    surface during adapter development (P1-T4+).
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


# ---------------------------------------------------------------------------
# Free / disposable email domains (PRD § Inputs: work email "must not be
# disposable/free-email-only"; § Risk Signals: disposable/free email domains)
# ---------------------------------------------------------------------------

FREE_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "hotmail.com",
        "hotmail.co.uk",
        "outlook.com",
        "live.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "tutanota.com",
        "mailinator.com",
        "guerrillamail.com",
        "throwam.com",
        "temp-mail.org",
        "yopmail.com",
        "sharklasers.com",
        "guerrillamailblock.com",
        "grr.la",
        "guerrillamail.info",
        "guerrillamail.biz",
        "guerrillamail.de",
        "guerrillamail.net",
        "guerrillamail.org",
        "spam4.me",
        "trashmail.com",
        "trashmail.me",
        "trashmail.net",
        "dispostable.com",
    }
)


def is_free_email_domain(email: str) -> bool:
    """Return True if the email uses a known free/disposable domain."""
    try:
        domain = email.split("@", 1)[1].lower()
    except IndexError:
        return False
    return domain in FREE_EMAIL_DOMAINS


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
            # Identity corroboration inputs (IC1-T4), passed through as given.
            "linkedin_url": (
                sub.linkedin_url.strip() if sub and sub.linkedin_url else None
            ),
            "requester_full_name": (
                sub.requester_full_name.strip()
                if sub and sub.requester_full_name
                else None
            ),
            # Network metadata — captured server-side; used by EnrichNetworkIPStage.
            "source_ip": (sub.source_ip if sub else None),
        }

        # Intake-derived evidence (tier 0: facts about the submission itself,
        # not an external source — excluded from source-coverage confidence).
        if email and is_free_email_domain(email):
            from datetime import datetime, timezone  # noqa: PLC0415

            from app.models.evidence import Evidence  # noqa: PLC0415

            db.add(
                Evidence(
                    verification_run_id=run_id,
                    source="submission",
                    tier=0,
                    field="free_email_domain",
                    raw_value="true",
                    normalized_value="true",
                    confidence=1.0,
                    raw_payload={"email_domain": email.split("@", 1)[1]},
                    attribution={"provider": "submission"},
                    fetched_at=datetime.now(tz=timezone.utc),
                )
            )
            db.commit()

        return {**context, "normalized": normalized}
