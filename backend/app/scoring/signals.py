"""Full PRD signal catalog for four-layer risk scoring (P2-T6).

This module maps evidence field values (as produced by the adapters) to named
Signal objects for each of the four scoring layers defined in
PRD § Core Verification Philosophy:

  Layer 1 — Entity legitimacy        (entity_score)
  Layer 2 — Infrastructure legitimacy (infrastructure_score)
  Layer 3 — Representation confidence (representation_score)
  Layer 4 — Fraud/staging risk        (risk_score)

Each layer function returns list[Signal].

Signal catalog design rules
----------------------------
1. Every Signal that references a *finding* MUST include ≥1 evidence_id.
   Signals that explain the *absence* of evidence (e.g. no_registry_evidence)
   may have evidence_ids=[] — they document what is missing.
2. Trust signals decrease the layer risk score.
3. Elevated signals increase the layer risk score.
4. Weights are in [0.0, 1.0] — the layer-score formula multiplies by 40.
5. The full PRD § Risk Signals catalog is covered here:
   Elevated: recently_registered_domain, no_mx_records, disposable/free email,
   registry_mismatch, domain_country_mismatch, billing_address_mismatch,
   thin/generated website, no_employee_footprint, datacenter/anonymized IP,
   IP_country_mismatch, ip_asn_reuse (cross-submission), sanctions_hit,
   conflicting_company_identities.
   NOT yet implemented (PRD): suspicious_dns_infrastructure,
   inconsistent_contact_information, recently created social presence.
   Trust: long_lived_domain, registry_confirmed, consistent_addresses,
   tax_id_verified_active, active_employee_footprint, matching_contact_info,
   stable_web_presence, ssl_present, spf_configured, has_mx_records.

Cross-submission IP/ASN reuse (deferred from P2-T2)
------------------------------------------------------
``extract_ip_asn_reuse_signals()`` queries prior submissions' evidence for the
same IP or ASN.  It takes an open SQLAlchemy Session so it can query the DB.
The function is called by fraud_staging_risk_signals when a session is supplied.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.scoring.engine import Signal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 1 — Entity legitimacy
# ---------------------------------------------------------------------------


def entity_legitimacy_signals(evidence_rows: list) -> list[Signal]:
    """Derive entity-legitimacy signals from evidence.

    Covers PRD § Risk Signals (elevated):
      - registry_mismatch (no Tier-1 name match)
      - missing / unavailable authoritative evidence
      - sanctions_hit (entity-level)
    And PRD trust:
      - registry_name_confirmed
      - registration_number_present
      - registration_status_active
      - tax_id_verified_active (IC1-T3; replaces the unreachable valid_tax_id)
    And elevated (IC1-T3): tax_id_not_found, tax_id_name_mismatch,
    tax_id_inactive_or_dissolved.
      - sanctions_cleared
    """
    signals: list[Signal] = []

    # Tax-ID rows are Tier 1 too, but they aren't registry evidence; they get
    # their own signals (IC1-T3) and must not shift the registry checks below.
    tier1_evidence = [e for e in evidence_rows if e.tier == 1 and e.source != "tax_id"]
    name_evidence = [e for e in tier1_evidence if e.field == "company_name"]
    reg_number_evidence = [
        e for e in tier1_evidence if e.field == "registration_number"
    ]
    status_evidence = [e for e in tier1_evidence if e.field == "registration_status"]
    sanctions_hit_evidence = [e for e in tier1_evidence if e.field == "sanctions_hit"]
    sanctions_screened_evidence = [
        e for e in tier1_evidence if e.field == "sanctions_screened"
    ]
    sanctions_flag_evidence = [
        e for e in tier1_evidence if e.field == "sanctions_risk_flag"
    ]

    # --- Sanctions signals (strong, always checked first) ---
    if sanctions_hit_evidence or sanctions_flag_evidence:
        ev_ids = [e.id for e in sanctions_hit_evidence + sanctions_flag_evidence]
        signals.append(
            Signal(
                name="sanctions_hit",
                layer="entity",
                direction="elevated",
                weight=1.0,
                description=(
                    "Entity name matched on the OFAC Specially Designated Nationals "
                    "(SDN) sanctions list — this is a critical risk flag."
                ),
                evidence_ids=ev_ids,
            )
        )
    elif sanctions_screened_evidence:
        signals.append(
            Signal(
                name="sanctions_cleared",
                layer="entity",
                direction="trust",
                weight=0.2,
                description=(
                    "Entity name screened against the OFAC SDN list — no match found."
                ),
                evidence_ids=[e.id for e in sanctions_screened_evidence],
            )
        )

    # --- Registry evidence ---
    if not tier1_evidence:
        signals.append(
            Signal(
                name="no_registry_evidence",
                layer="entity",
                direction="elevated",
                weight=1.0,
                description=(
                    "No Tier-1 (authoritative registry) evidence found for this entity."
                ),
                evidence_ids=[],
            )
        )
        signals.extend(_tax_id_signals(evidence_rows))
        signals.extend(_linkedin_entity_signals(evidence_rows))
        return signals

    # Registry name confirmed / mismatch
    if name_evidence:
        signals.append(
            Signal(
                name="registry_name_confirmed",
                layer="entity",
                direction="trust",
                weight=0.6,
                description="Company name found in authoritative registry (Tier-1).",
                evidence_ids=[e.id for e in name_evidence],
            )
        )
    else:
        signals.append(
            Signal(
                name="registry_name_unconfirmed",
                layer="entity",
                direction="elevated",
                weight=0.4,
                description="Tier-1 source did not return a matching company name.",
                evidence_ids=[e.id for e in tier1_evidence[:1]],
            )
        )

    # Registration number
    if reg_number_evidence:
        signals.append(
            Signal(
                name="registration_number_present",
                layer="entity",
                direction="trust",
                weight=0.4,
                description=(
                    "Registration number found in authoritative registry — "
                    "strong entity confirmation."
                ),
                evidence_ids=[e.id for e in reg_number_evidence],
            )
        )

    # Registration status
    if status_evidence:
        active_statuses = {"active", "incorporated", "live", "registered"}
        active_ev = [
            e
            for e in status_evidence
            if e.normalized_value
            and any(s in (e.normalized_value or "").lower() for s in active_statuses)
        ]
        if active_ev:
            signals.append(
                Signal(
                    name="registry_status_active",
                    layer="entity",
                    direction="trust",
                    weight=0.3,
                    description=(
                        "Registry status indicates company is active/incorporated."
                    ),
                    evidence_ids=[e.id for e in active_ev],
                )
            )
        else:
            signals.append(
                Signal(
                    name="registry_status_inactive",
                    layer="entity",
                    direction="elevated",
                    weight=0.3,
                    description=(
                        "Registry status found but does not indicate active status."
                    ),
                    evidence_ids=[e.id for e in status_evidence[:1]],
                )
            )

    # Multiple distinct registered entities share the submitted name (P4-T2).
    conflict_ev = [e for e in tier1_evidence if e.field == "registry_identity_conflict"]
    if conflict_ev:
        signals.append(
            Signal(
                name="conflicting_company_identities",
                layer="entity",
                direction="elevated",
                weight=0.5,
                description=(
                    "Several distinct registered entities carry exactly this name — "
                    "confirm which one the applicant is."
                ),
                evidence_ids=[e.id for e in conflict_ev],
            )
        )

    signals.extend(_tax_id_signals(evidence_rows))
    signals.extend(_linkedin_entity_signals(evidence_rows))
    return signals


def _tax_id_signals(evidence_rows: list) -> list[Signal]:
    """Entity-layer signals from the tax-ID/FEIN source (IC1-T3).

    No tax-ID evidence (no input, non-US, provider down or not configured)
    contributes nothing: an unavailable source lowers coverage, not the score.
    """
    rows = [e for e in evidence_rows if e.source == "tax_id"]
    status_ev = [e for e in rows if e.field == "tax_id_status"]
    match_ev = [e for e in rows if e.field == "tax_id_name_match"]
    if not status_ev:
        return []

    status = (status_ev[0].normalized_value or "").lower()
    match = (match_ev[0].normalized_value or "").lower() if match_ev else ""
    out: list[Signal] = []

    if status == "not_found":
        out.append(
            Signal(
                name="tax_id_not_found",
                layer="entity",
                direction="elevated",
                weight=0.5,
                description=(
                    "The submitted tax ID (FEIN) does not resolve with the "
                    "authoritative tax-ID source."
                ),
                evidence_ids=[status_ev[0].id],
            )
        )
        return out

    if status == "inactive":
        out.append(
            Signal(
                name="tax_id_inactive_or_dissolved",
                layer="entity",
                direction="elevated",
                weight=0.4,
                description="The tax ID is on file but the entity is not active.",
                evidence_ids=[status_ev[0].id],
            )
        )
    if match == "mismatch":
        out.append(
            Signal(
                name="tax_id_name_mismatch",
                layer="entity",
                direction="elevated",
                weight=0.5,
                description=(
                    "The tax ID is registered to a different name than the "
                    "submitted company name."
                ),
                evidence_ids=[match_ev[0].id],
            )
        )
    if status == "verified" and match == "match":
        out.append(
            Signal(
                name="tax_id_verified_active",
                layer="entity",
                direction="trust",
                weight=0.5,
                description=(
                    "Tax ID verified with an authoritative source: active and "
                    "registered to the submitted company name."
                ),
                evidence_ids=[status_ev[0].id, match_ev[0].id],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Layer 2 — Infrastructure legitimacy
# ---------------------------------------------------------------------------


def infrastructure_legitimacy_signals(evidence_rows: list) -> list[Signal]:
    """Derive infrastructure-legitimacy signals from evidence.

    Covers PRD § Risk Signals (elevated):
      - recently_registered_domain
      - no_mx_records (no email infrastructure)
    And PRD trust:
      - long_lived_domain (3+ years)
      - established_domain (1–3 years)
      - has_mx_records
      - spf_configured
      - dkim_configured
      - ssl_present
    """
    signals: list[Signal] = []

    tier2_evidence = [e for e in evidence_rows if e.tier == 2]

    if not tier2_evidence:
        signals.append(
            Signal(
                name="infrastructure_data_unavailable",
                layer="infrastructure",
                direction="elevated",
                weight=0.3,
                description=(
                    "No Tier-2 (domain/infrastructure) evidence available. "
                    "Score reflects reduced confidence, not confirmed risk."
                ),
                evidence_ids=[],
            )
        )
        return signals

    domain_age_evidence = [e for e in tier2_evidence if e.field == "domain_age_days"]
    mx_evidence = [e for e in tier2_evidence if e.field == "mx_present"]
    spf_evidence = [e for e in tier2_evidence if e.field == "spf_present"]
    ssl_evidence = [e for e in tier2_evidence if e.field in ("ssl_issuer", "ssl_valid")]
    recently_registered_evidence = [
        e for e in tier2_evidence if e.field == "recently_registered"
    ]
    no_mx_evidence = [e for e in tier2_evidence if e.field == "no_mx"]
    dkim_evidence = [e for e in tier2_evidence if e.field == "dkim_present"]

    # --- Domain age signals ---
    if domain_age_evidence:
        best_age_ev = domain_age_evidence[0]
        try:
            age_days = int(best_age_ev.normalized_value or best_age_ev.raw_value or "0")
        except (ValueError, TypeError):
            age_days = 0

        if age_days >= 365 * 3:
            signals.append(
                Signal(
                    name="long_lived_domain",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.5,
                    description=(
                        f"Domain is {age_days} days old (≥3 years) — stable presence."
                    ),
                    evidence_ids=[best_age_ev.id],
                )
            )
        elif age_days >= 365:
            signals.append(
                Signal(
                    name="established_domain",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.25,
                    description=f"Domain is {age_days} days old (≥1 year).",
                    evidence_ids=[best_age_ev.id],
                )
            )

    # --- Recently registered (elevated risk) ---
    if recently_registered_evidence:
        ev = recently_registered_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in (
            "true",
            "1",
            "yes",
        ):
            signals.append(
                Signal(
                    name="recently_registered_domain",
                    layer="infrastructure",
                    direction="elevated",
                    weight=0.6,
                    description=(
                        "Domain was registered within the last 180 days — "
                        "a leading indicator of staged/fraudulent infrastructure."
                    ),
                    evidence_ids=[ev.id],
                )
            )

    # --- MX records (email infrastructure) ---
    if mx_evidence:
        ev = mx_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in (
            "true",
            "1",
            "yes",
        ):
            signals.append(
                Signal(
                    name="has_mx_records",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.3,
                    description=(
                        "Domain has MX records — organization controls email "
                        "infrastructure."
                    ),
                    evidence_ids=[ev.id],
                )
            )

    if no_mx_evidence:
        ev = no_mx_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in (
            "true",
            "1",
            "yes",
        ):
            signals.append(
                Signal(
                    name="no_mx_records",
                    layer="infrastructure",
                    direction="elevated",
                    weight=0.4,
                    description=(
                        "Domain has no MX records — no email infrastructure detected. "
                        "PRD § Risk Signals: 'No MX records' elevated-risk indicator."
                    ),
                    evidence_ids=[ev.id],
                )
            )

    # --- SPF (email authentication) ---
    if spf_evidence:
        ev = spf_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in (
            "true",
            "1",
            "yes",
        ):
            signals.append(
                Signal(
                    name="spf_configured",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.2,
                    description=(
                        "Domain has SPF record — email authentication configured."
                    ),
                    evidence_ids=[ev.id],
                )
            )

    # --- DKIM (email authentication) ---
    if dkim_evidence:
        ev = dkim_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in (
            "true",
            "1",
            "yes",
        ):
            signals.append(
                Signal(
                    name="dkim_configured",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.15,
                    description=(
                        "Domain has DKIM record — email authentication configured."
                    ),
                    evidence_ids=[ev.id],
                )
            )

    # --- SSL certificate ---
    if ssl_evidence:
        signals.append(
            Signal(
                name="ssl_present",
                layer="infrastructure",
                direction="trust",
                weight=0.2,
                description=(
                    "Domain has an SSL/TLS certificate — basic security infrastructure."
                ),
                evidence_ids=[e.id for e in ssl_evidence[:1]],
            )
        )

    return signals


# ---------------------------------------------------------------------------
# Layer 3 — Representation confidence
# ---------------------------------------------------------------------------


def representation_confidence_signals(
    evidence_rows: list, field_comparisons: list
) -> list[Signal]:
    """Derive representation-confidence signals from field comparisons + evidence.

    Covers PRD § Risk Signals (elevated):
      - registry_mismatch (name, country, address)
      - billing_address_mismatch
      - domain_country_mismatch (via ip_country_mismatch)
      - free_email_domain (tier-0 intake evidence)
    And PRD trust:
      - consistent_name, consistent_address, consistent_jurisdiction
      - ip_country_match
      - web_contact_email_found
    """
    signals: list[Signal] = []

    fc_by_field: dict[str, object] = {fc.field_name: fc for fc in field_comparisons}

    if not fc_by_field:
        signals.append(
            Signal(
                name="no_field_comparisons",
                layer="representation",
                direction="elevated",
                weight=0.3,
                description=(
                    "No field comparisons were available. "
                    "Confidence is reduced but this does not confirm misrepresentation."
                ),
                evidence_ids=[],
            )
        )
        # Still check IP/web signals — these are direct evidence signals,
        # not field-comparison-derived.
        _append_ip_signals(signals, evidence_rows)
        _append_web_contact_signals(signals, evidence_rows)
        _append_intake_signals(signals, evidence_rows)
        _append_linkedin_signals(signals, evidence_rows, fc_by_field)
        return signals

    # Core field weights (PRD § Core Verification Philosophy — representation layer)
    field_labels = {
        "company_name": ("Company name", 0.5),
        "country_iso": ("Jurisdiction/country", 0.3),
        "billing_address": ("Billing address", 0.25),
    }

    for field_name, (label, weight) in field_labels.items():
        fc = fc_by_field.get(field_name)
        if fc is None:
            continue

        ev_ids = [fc.evidence_id] if fc.evidence_id else []

        if fc.match_status == "match":
            signals.append(
                Signal(
                    name=f"consistent_{field_name}",
                    layer="representation",
                    direction="trust",
                    weight=weight,
                    description=f"{label} matches discovered registry data.",
                    evidence_ids=ev_ids,
                )
            )
        elif fc.match_status == "mismatch":
            signals.append(
                Signal(
                    name=f"{field_name}_mismatch",
                    layer="representation",
                    direction="elevated",
                    weight=weight,
                    description=(
                        f"{label} does not match discovered registry data — "
                        f"PRD § Risk Signals: 'Registry mismatch'. "
                        f"Submitted: {fc.submitted_value!r}, "
                        f"Discovered: {fc.discovered_value!r}."
                    ),
                    evidence_ids=ev_ids,
                )
            )
        # "unverified" contributes no signal (insufficient data)

    _append_ip_signals(signals, evidence_rows)
    _append_web_contact_signals(signals, evidence_rows)
    _append_intake_signals(signals, evidence_rows)
    _append_linkedin_signals(signals, evidence_rows, fc_by_field)

    # Guard: if no signals produced (all fields unverified)
    if not signals:
        signals.append(
            Signal(
                name="all_fields_unverified",
                layer="representation",
                direction="elevated",
                weight=0.4,
                description=(
                    "All compared fields are unverified — "
                    "no discovered data to compare against."
                ),
                evidence_ids=[],
            )
        )

    return signals


# LinkedIn footprint thresholds (IC1-T5). "Established" needs a real following;
# "thin" means both counts are near zero. Anything between adds no signal.
_LI_ESTABLISHED_EMPLOYEES = 10
_LI_ESTABLISHED_FOLLOWERS = 500
_LI_THIN_EMPLOYEES = 5
_LI_THIN_FOLLOWERS = 50
_LI_RECENT_DAYS = 365
# Tier 3: absence is weak evidence (PRD §6 calibration note).
_LI_ABSENT_WEIGHT = 0.15


def _li_rows(evidence_rows: list) -> dict:
    return {
        e.field: e
        for e in evidence_rows
        if e.source == "linkedin" and (e.field or "").startswith("linkedin_")
    }


def _li_int(ev) -> int | None:
    try:
        return int(ev.normalized_value) if ev is not None else None
    except (TypeError, ValueError):
        return None


def _li_footprint(rows: dict) -> tuple[bool, bool]:
    """(established, thin) from employee count and followers."""
    employees = _li_int(rows.get("linkedin_employee_count"))
    followers = _li_int(rows.get("linkedin_followers"))
    established = (employees or 0) >= _LI_ESTABLISHED_EMPLOYEES or (
        followers or 0
    ) >= _LI_ESTABLISHED_FOLLOWERS
    thin = (
        employees is not None
        and followers is not None
        and employees < _LI_THIN_EMPLOYEES
        and followers < _LI_THIN_FOLLOWERS
    )
    return established, thin


def _append_linkedin_signals(
    signals: list[Signal], evidence_rows: list, fc_by_field: dict
) -> None:
    """Representation-layer LinkedIn signals (IC1-T5). Tier 3: weights stay low.

    No LinkedIn evidence (unresolved search, provider not configured) adds
    nothing.
    """
    rows = _li_rows(evidence_rows)
    presence = rows.get("linkedin_presence")
    if presence is None:
        return

    if (presence.normalized_value or "") != "found":
        signals.append(
            Signal(
                name="linkedin_absent_or_thin",
                layer="representation",
                direction="elevated",
                weight=_LI_ABSENT_WEIGHT,
                description=(
                    "The submitted LinkedIn company page does not exist. Weak "
                    "evidence on its own; many legitimate firms have thin pages."
                ),
                evidence_ids=[presence.id],
            )
        )
        return

    established, thin = _li_footprint(rows)
    website_fc = fc_by_field.get("linkedin_website")

    if thin:
        signals.append(
            Signal(
                name="linkedin_absent_or_thin",
                layer="representation",
                direction="elevated",
                weight=_LI_ABSENT_WEIGHT,
                description=(
                    "The LinkedIn company page has almost no employees or "
                    "followers. Weak evidence on its own."
                ),
                evidence_ids=[
                    e.id
                    for e in (
                        presence,
                        rows.get("linkedin_employee_count"),
                        rows.get("linkedin_followers"),
                    )
                    if e is not None
                ],
            )
        )

    if website_fc is not None and website_fc.match_status == "mismatch":
        signals.append(
            Signal(
                name="linkedin_website_mismatch",
                layer="representation",
                direction="elevated",
                weight=0.25,
                description=(
                    "The LinkedIn page lists a different website than the "
                    f"submitted domain ({website_fc.discovered_value!r} vs. "
                    f"{website_fc.submitted_value!r})."
                ),
                evidence_ids=[website_fc.evidence_id]
                if website_fc.evidence_id
                else [presence.id],
            )
        )
    elif established and website_fc is not None and website_fc.match_status == "match":
        signals.append(
            Signal(
                name="linkedin_established_presence",
                layer="representation",
                direction="trust",
                weight=0.3,
                description=(
                    "Established LinkedIn company page whose stated website "
                    "matches the submitted domain."
                ),
                evidence_ids=[presence.id]
                + ([website_fc.evidence_id] if website_fc.evidence_id else []),
            )
        )

    created = rows.get("linkedin_page_created")
    if created is not None:
        from datetime import date  # noqa: PLC0415

        try:
            age = (date.today() - date.fromisoformat(created.normalized_value)).days
        except (TypeError, ValueError):
            age = None
        if age is not None and 0 <= age < _LI_RECENT_DAYS:
            signals.append(
                Signal(
                    name="linkedin_recently_created",
                    layer="representation",
                    direction="elevated",
                    weight=0.2,
                    description=(
                        f"The LinkedIn company page was created {age} days ago "
                        "(PRD § Risk Signals: recently created social presence)."
                    ),
                    evidence_ids=[created.id],
                )
            )

    requester = rows.get("linkedin_requester_match")
    if requester is not None and (requester.normalized_value or "") == "true":
        signals.append(
            Signal(
                name="linkedin_requester_associated",
                layer="representation",
                direction="trust",
                weight=0.2,
                description="The requester is associated with the company on LinkedIn.",
                evidence_ids=[requester.id],
            )
        )


def _linkedin_entity_signals(evidence_rows: list) -> list[Signal]:
    """Secondary entity-layer trust from an established LinkedIn page (IC1-T5).

    Deliberately tiny (Tier 3): it corroborates, never confirms, the entity.
    """
    rows = _li_rows(evidence_rows)
    presence = rows.get("linkedin_presence")
    if presence is None or (presence.normalized_value or "") != "found":
        return []
    established, _thin = _li_footprint(rows)
    if not established:
        return []
    return [
        Signal(
            name="linkedin_presence_corroborates_entity",
            layer="entity",
            direction="trust",
            weight=0.1,
            description="An established LinkedIn page corroborates the entity.",
            evidence_ids=[presence.id],
        )
    ]


def _append_ip_signals(signals: list[Signal], evidence_rows: list) -> None:
    """Append IP country mismatch/match signals to representation signals list.

    Extracted to avoid code duplication between the 'no field comparisons'
    early-return path and the normal field-comparison path.
    """
    ip_country_mismatch_ev = [
        e for e in evidence_rows if e.field == "ip_country_mismatch"
    ]
    if ip_country_mismatch_ev:
        ev = ip_country_mismatch_ev[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in (
            "true",
            "1",
            "yes",
        ):
            signals.append(
                Signal(
                    name="ip_country_mismatch",
                    layer="representation",
                    direction="elevated",
                    weight=0.3,
                    description=(
                        "IP country does not match the submitted company/billing "
                        "country — PRD § Risk Signals: 'Domain-country mismatch'."
                    ),
                    evidence_ids=[ev.id],
                )
            )
    else:
        ip_country_match_ev = [
            e
            for e in evidence_rows
            if e.field == "ip_country_match"
            and (e.normalized_value or e.raw_value or "").lower()
            in ("true", "1", "yes")
        ]
        if ip_country_match_ev:
            signals.append(
                Signal(
                    name="ip_country_match",
                    layer="representation",
                    direction="trust",
                    weight=0.2,
                    description="IP country matches the submitted company country.",
                    evidence_ids=[e.id for e in ip_country_match_ev[:1]],
                )
            )


def _append_intake_signals(signals: list[Signal], evidence_rows: list) -> None:
    """Signals from facts about the submission itself (tier-0 evidence)."""
    free_email = [e for e in evidence_rows if e.field == "free_email_domain"]
    if free_email:
        signals.append(
            Signal(
                name="free_email_domain",
                layer="representation",
                direction="elevated",
                weight=0.3,
                description=(
                    "Registered with a free/disposable email address rather than "
                    "one on the company's domain."
                ),
                evidence_ids=[e.id for e in free_email],
            )
        )


def _append_web_contact_signals(signals: list[Signal], evidence_rows: list) -> None:
    """Append web contact signals to representation signals list."""
    # Only an address on the company's own domain is evidence the organization
    # runs this site; a vendor's or customer's email on the page is not.
    web_email_ev = [
        e
        for e in evidence_rows
        if e.field == "web_contacts_email"
        and (e.raw_payload or {}).get("on_company_domain") is True
    ]
    if web_email_ev:
        signals.append(
            Signal(
                name="web_contact_email_found",
                layer="representation",
                direction="trust",
                weight=0.15,
                description="Company-domain contact email found on the website.",
                evidence_ids=[e.id for e in web_email_ev[:1]],
            )
        )


# ---------------------------------------------------------------------------
# Layer 4 — Fraud/staging risk
# ---------------------------------------------------------------------------


def fraud_staging_risk_signals(
    evidence_rows: list,
    run_id: str | None = None,
    db: "Session | None" = None,
) -> list[Signal]:
    """Derive fraud/staging risk signals (cross-cutting synthesis layer).

    This layer synthesizes signals across all tiers.  Covers PRD § Risk Signals:

    Elevated:
      - recently_registered_no_mx (combined pattern = strong staging signal)
      - recently_registered_domain_risk
      - no_mx_risk
      - thin_website / no_employee_footprint
      - datacenter_ip / anonymized_network / vpn_proxy (ip_anonymized_network)
      - ip_asn_reuse (cross-submission — deferred from P2-T2, implemented here)
      - sanctions_hit (fraud/evasion layer view)
      - ip_suspicious_asn

    Trust:
      - long_lived_domain_trust
      - active_employee_footprint
      - stable_web_presence

    Cross-submission IP/ASN reuse:
      When ``db`` and ``run_id`` are provided, the function queries Evidence
      rows from OTHER runs that share the same IP or ASN, emitting an elevated
      signal if reuse is detected.  This implements the deferred P2-T2 item.
    """
    signals: list[Signal] = []

    tier2_evidence = [e for e in evidence_rows if e.tier == 2]
    tier3_evidence = [e for e in evidence_rows if e.tier == 3]

    # Gather specific evidence fields
    recently_registered_evidence = [
        e for e in tier2_evidence if e.field == "recently_registered"
    ]
    no_mx_evidence = [e for e in tier2_evidence if e.field == "no_mx"]
    domain_age_evidence = [e for e in tier2_evidence if e.field == "domain_age_days"]

    # IP network risk evidence (Tier-2, from IPinfo)
    ip_anonymized_ev = [e for e in evidence_rows if e.field == "ip_anonymized_network"]
    ip_hosting_ev = [e for e in evidence_rows if e.field == "ip_hosting"]
    ip_vpn_ev = [e for e in evidence_rows if e.field == "ip_vpn"]
    ip_proxy_ev = [e for e in evidence_rows if e.field == "ip_proxy"]
    ip_suspicious_asn_ev = [e for e in evidence_rows if e.field == "ip_suspicious_asn"]
    ip_asn_ev = [e for e in evidence_rows if e.field == "ip_asn"]

    # Sanctions hit (fraud/evasion lens)
    sanctions_hit_ev = [e for e in evidence_rows if e.field == "sanctions_hit"]
    sanctions_risk_ev = [e for e in evidence_rows if e.field == "sanctions_risk_flag"]

    # Web presence signals (Tier-3)
    thin_footprint_ev = [e for e in tier3_evidence if e.field == "web_thin_footprint"]
    employee_footprint_ev = [
        e for e in tier3_evidence if e.field == "web_employee_footprint"
    ]

    # --- Sanctions hit is a critical fraud/evasion signal ---
    if sanctions_hit_ev or sanctions_risk_ev:
        ev_ids = [e.id for e in sanctions_hit_ev + sanctions_risk_ev]
        signals.append(
            Signal(
                name="sanctions_hit_fraud_flag",
                layer="risk",
                direction="elevated",
                weight=1.0,
                description=(
                    "Sanctions list hit — potential sanctions evasion attempt. "
                    "PRD § Risk Signals: critical fraud/staging risk."
                ),
                evidence_ids=ev_ids,
            )
        )

    # --- Staging pattern: recently registered + no MX ---
    recently_reg = any(
        (e.normalized_value or e.raw_value or "").lower() in ("true", "1", "yes")
        for e in recently_registered_evidence
    )
    no_mx = any(
        (e.normalized_value or e.raw_value or "").lower() in ("true", "1", "yes")
        for e in no_mx_evidence
    )

    if recently_reg and no_mx:
        ev_ids = [e.id for e in recently_registered_evidence + no_mx_evidence]
        signals.append(
            Signal(
                name="recently_registered_no_mx",
                layer="risk",
                direction="elevated",
                weight=0.7,
                description=(
                    "Domain was recently registered AND has no MX records — "
                    "consistent with staged/fraudulent infrastructure. "
                    "PRD § Risk Signals: 'Recently registered domain' + 'No MX'."
                ),
                evidence_ids=ev_ids,
            )
        )
    elif recently_reg:
        ev_ids = [e.id for e in recently_registered_evidence]
        signals.append(
            Signal(
                name="recently_registered_domain_risk",
                layer="risk",
                direction="elevated",
                weight=0.4,
                description=(
                    "Recently registered domain is a fraud/staging risk signal."
                ),
                evidence_ids=ev_ids,
            )
        )
    elif no_mx:
        ev_ids = [e.id for e in no_mx_evidence]
        signals.append(
            Signal(
                name="no_mx_risk",
                layer="risk",
                direction="elevated",
                weight=0.3,
                description=(
                    "No MX records — domain may lack legitimate email infrastructure."
                ),
                evidence_ids=ev_ids,
            )
        )

    # --- Trust: long-lived domain reduces fraud risk ---
    if domain_age_evidence:
        best_age_ev = domain_age_evidence[0]
        try:
            age_days = int(best_age_ev.normalized_value or best_age_ev.raw_value or "0")
        except (ValueError, TypeError):
            age_days = 0

        if age_days >= 365 * 3:
            signals.append(
                Signal(
                    name="long_lived_domain_trust",
                    layer="risk",
                    direction="trust",
                    weight=0.5,
                    description=(
                        f"Domain age ({age_days} days) reduces fraud/staging risk."
                    ),
                    evidence_ids=[best_age_ev.id],
                )
            )

    # --- Datacenter / anonymized network ---
    anonymized_ev = ip_anonymized_ev or ip_hosting_ev or ip_vpn_ev or ip_proxy_ev
    if anonymized_ev:
        all_ev_ids = list(
            {e.id for e in ip_anonymized_ev + ip_hosting_ev + ip_vpn_ev + ip_proxy_ev}
        )
        signals.append(
            Signal(
                name="datacenter_or_anonymized_ip",
                layer="risk",
                direction="elevated",
                weight=0.5,
                description=(
                    "Submission originated from a datacenter, VPN, or anonymizing "
                    "proxy — PRD § Network & IP Intelligence: 'Datacenter or "
                    "anonymized network usage'."
                ),
                evidence_ids=all_ev_ids,
            )
        )

    # --- Suspicious ASN (known hostile network ownership) ---
    if ip_suspicious_asn_ev:
        signals.append(
            Signal(
                name="suspicious_asn",
                layer="risk",
                direction="elevated",
                weight=0.3,
                description=(
                    "Submission IP belongs to an ASN associated with suspicious or "
                    "adversarial network ownership."
                ),
                evidence_ids=[e.id for e in ip_suspicious_asn_ev],
            )
        )

    # --- Cross-submission IP/ASN reuse (deferred from P2-T2) ---
    # Requires a DB session and run_id to query prior submissions' evidence.
    if db is not None and run_id is not None and ip_asn_ev:
        reuse_signals = _cross_submission_reuse_signals(
            run_id=run_id,
            db=db,
            ip_asn_evidence=ip_asn_ev,
            all_evidence=evidence_rows,
        )
        signals.extend(reuse_signals)

    # --- Thin website / no employee footprint ---
    if thin_footprint_ev:
        signals.append(
            Signal(
                name="thin_website",
                layer="risk",
                direction="elevated",
                weight=0.35,
                description=(
                    "Company website appears thin/generated — no email contacts or "
                    "content extracted. PRD § Risk Signals: 'Thin/generated website' "
                    "and 'No employee footprint'."
                ),
                evidence_ids=[e.id for e in thin_footprint_ev],
            )
        )
    elif employee_footprint_ev:
        signals.append(
            Signal(
                name="active_employee_footprint",
                layer="risk",
                direction="trust",
                weight=0.3,
                description=(
                    "Active employee footprint detected on company website. "
                    "PRD § Trust Signals: 'Active employee footprint'."
                ),
                evidence_ids=[e.id for e in employee_footprint_ev],
            )
        )

    # --- Stable web presence (brand found, overall trust signal) ---
    web_brand_ev = [e for e in tier3_evidence if e.field == "web_brand"]
    if web_brand_ev and not thin_footprint_ev:
        signals.append(
            Signal(
                name="stable_web_presence",
                layer="risk",
                direction="trust",
                weight=0.2,
                description=(
                    "Company website has identifiable branding — consistent "
                    "web presence. PRD § Trust Signals: 'Stable web presence'."
                ),
                evidence_ids=[e.id for e in web_brand_ev[:1]],
            )
        )

    # --- Fallback: no data ---
    if not signals:
        if not tier2_evidence and not tier3_evidence:
            signals.append(
                Signal(
                    name="fraud_signals_unavailable",
                    layer="risk",
                    direction="elevated",
                    weight=0.2,
                    description=(
                        "No infrastructure or web data available to assess "
                        "fraud/staging risk. Score reflects uncertainty, not "
                        "confirmed risk."
                    ),
                    evidence_ids=[],
                )
            )
        else:
            combined = tier2_evidence + tier3_evidence
            signals.append(
                Signal(
                    name="no_fraud_signals_detected",
                    layer="risk",
                    direction="trust",
                    weight=0.5,
                    description=(
                        "No fraud or staging risk signals detected from available "
                        "evidence."
                    ),
                    evidence_ids=[e.id for e in combined[:2]],
                )
            )

    return signals


# ---------------------------------------------------------------------------
# Cross-submission IP/ASN reuse detection (deferred from P2-T2)
# ---------------------------------------------------------------------------


def _cross_submission_reuse_signals(
    run_id: str,
    db: "Session",
    ip_asn_evidence: list,
    all_evidence: list,  # noqa: ARG001 — reserved for future IP-level reuse
) -> list[Signal]:
    """Query prior submissions for IP/ASN reuse and emit risk signals.

    Searches Evidence rows from OTHER runs that share the same ip_asn value
    (same ASN = same network operator).

    A single reuse hit is unusual but not conclusive; multiple hits in a
    short window are a strong fraud/staging indicator.

    Returns a list of Signals — empty if no reuse detected.
    """
    from app.models.evidence import Evidence  # noqa: PLC0415

    signals: list[Signal] = []

    # Extract ASN values from current run's evidence
    current_asns: set[str] = set()
    for ev in ip_asn_evidence:
        val = (ev.normalized_value or ev.raw_value or "").strip()
        if val:
            current_asns.add(val.upper())

    if not current_asns:
        return signals

    try:
        # Query evidence rows from OTHER runs with matching ASN values
        reuse_evidence: list = []

        for asn in current_asns:
            prior_asn_ev = (
                db.query(Evidence)
                .filter(
                    Evidence.field == "ip_asn",
                    Evidence.normalized_value == asn,
                    Evidence.verification_run_id != run_id,
                )
                .limit(5)
                .all()
            )
            reuse_evidence.extend(prior_asn_ev)

        # De-duplicate by verification_run_id
        prior_run_ids: set[str] = {e.verification_run_id for e in reuse_evidence}
        reuse_count = len(prior_run_ids)

        if reuse_count >= 3:
            signals.append(
                Signal(
                    name="ip_asn_reuse_high",
                    layer="risk",
                    direction="elevated",
                    weight=0.6,
                    description=(
                        f"IP ASN appears in {reuse_count} other submission(s) — "
                        "high reuse suggests a coordinated registration campaign. "
                        "PRD § Network & IP Intelligence: 'Repeated registrations "
                        "from same IP/ASN'."
                    ),
                    evidence_ids=[e.id for e in ip_asn_evidence],
                )
            )
        elif reuse_count >= 1:
            signals.append(
                Signal(
                    name="ip_asn_reuse",
                    layer="risk",
                    direction="elevated",
                    weight=0.3,
                    description=(
                        f"IP ASN appears in {reuse_count} other submission(s) — "
                        "potential reuse pattern. PRD § Network & IP Intelligence: "
                        "'Repeated registrations from same IP/ASN'."
                    ),
                    evidence_ids=[e.id for e in ip_asn_evidence],
                )
            )

    except Exception as exc:
        logger.warning("_cross_submission_reuse_signals: DB query error: %s", exc)

    return signals
