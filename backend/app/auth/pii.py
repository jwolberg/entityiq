"""Role-gated PII access for the KYB report API (ADR-0002 §2; ticket 0002).

The assembled report (GET /reports/{run_id}, GET /reports/{run_id}/export)
mixes company-level evidence with personal data. The first category this
module gates: "raw network metadata" — evidence from the ipinfo (Tier-2)
adapter (app/adapters/ipinfo.py). Its normalized fields (ip_country, ip_asn,
...) are the *derived* signals ADR-0002 says operators may see; its
``attribution["source_url"]`` embeds the literal source IP the evidence was
fetched for (``https://ipinfo.io/<ip>/json``) — that's the "full IP" ADR-0002
restricts to leads. (Ticket 0020 extends this module with a second category:
LinkedIn requester-association evidence.)

Access matrix (ADR-0002 §2):
  lead      — sees everything: derived network signals AND the IP-identifying
              attribution.
  operator  — sees the derived network signals but not the raw IP.
  system    — an integration API key. No network-metadata evidence or
              sources at all (not even derived signals — "reports without
              raw network metadata"), and no contact-PII mismatch fields.
              Company-level evidence and scores only.

``filter_summary_for_viewer()`` is the single choke point app/api/reports.py
calls, for both GET /reports/{run_id} and GET /reports/{run_id}/export, so
the same policy governs both routes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.auth.service import Principal

Viewer = Literal["lead", "operator", "system"]

# Evidence source name for the Tier-2 network/IP intelligence adapter.
NETWORK_METADATA_SOURCE = "ipinfo"

# FieldComparison.field_name values that carry the submitter's own contact
# PII in `submitted_value` — as opposed to company-level fields such as
# company_name, domain, tax_id, or linkedin_website.
CONTACT_PII_MISMATCH_FIELDS = frozenset({"billing_address"})


def resolve_viewer(principal: "Principal", db: "Session") -> Viewer:
    """Map an authenticated Principal to its report-visibility tier.

    Fails safe: an operator principal whose Operator row can't be resolved
    (e.g. a stale session) is treated as "operator", never "lead" — it can
    only ever lose access to extra data, never gain it.
    """
    if principal.kind != "operator":
        return "system"
    if principal.operator_id:
        from app.models.operator import Operator  # noqa: PLC0415

        operator = db.get(Operator, principal.operator_id)
        if operator is not None and operator.role == "lead":
            return "lead"
    return "operator"


def _strip_network_attribution(item: dict) -> dict:
    """Drop the IP-identifying `attribution.source_url`; keep the provider name."""
    attribution = item.get("attribution")
    if not attribution:
        return item
    item = dict(item)
    item["attribution"] = {k: v for k, v in attribution.items() if k != "source_url"}
    return item


def filter_summary_for_viewer(summary: dict, viewer: Viewer) -> dict:
    """Return a copy of the report summary dict, redacted per ADR-0002 for `viewer`.

    Operates on the raw section lists stored on Report.summary — the shape
    assembled by app.scoring.report._build_summary() — before they're wrapped
    in the response schemas. Never mutates the input. Leaves `scores`
    untouched: it's an aggregate, never raw PII.
    """
    evidence = list(summary.get("evidence") or [])
    sources = list(summary.get("sources") or [])
    mismatches = list(summary.get("mismatches") or [])

    if viewer == "system":
        evidence = [e for e in evidence if e.get("source") != NETWORK_METADATA_SOURCE]
        sources = [s for s in sources if s.get("source") != NETWORK_METADATA_SOURCE]
        mismatches = [
            m
            for m in mismatches
            if m.get("field_name") not in CONTACT_PII_MISMATCH_FIELDS
        ]
    elif viewer == "operator":
        evidence = [
            _strip_network_attribution(e)
            if e.get("source") == NETWORK_METADATA_SOURCE
            else e
            for e in evidence
        ]
        sources = [
            _strip_network_attribution(s)
            if s.get("source") == NETWORK_METADATA_SOURCE
            else s
            for s in sources
        ]
    # viewer == "lead": no redaction — full access.

    filtered = dict(summary)
    filtered["evidence"] = evidence
    filtered["sources"] = sources
    filtered["mismatches"] = mismatches
    return filtered


def report_contains_pii(summary: dict) -> bool:
    """True if this report's evidence/mismatches carry any PII-classified data.

    Used to decide whether a report view needs a PII-view audit event
    (ADR-0002 §2: "Viewing a report detail that contains PII records an audit
    event"). Company-level facts (registry name, domain age, ...) don't
    count; network metadata and contact-PII mismatches do.
    """
    evidence = summary.get("evidence") or []
    if any(e.get("source") == NETWORK_METADATA_SOURCE for e in evidence):
        return True
    mismatches = summary.get("mismatches") or []
    return any(m.get("field_name") in CONTACT_PII_MISMATCH_FIELDS for m in mismatches)
