"""Public-records detail reaches the report API, redacted for API keys (0095).

The panel needs each record's raw_payload (docket, court, agency, dates).
Operators see the full payload; integration API keys get person names
replaced (ADR-0007, app.auth.pii).
"""

from app.api.reports import _serialize_report
from app.auth.pii import REDACTED
from app.models.report import Report


def _item(kind, match, party, **extra):
    return {
        "id": f"ev-{kind}-{match}",
        "source": "public_records",
        "tier": 1,
        "field": "public_record",
        "raw_value": kind,
        "normalized_value": match,
        "confidence": 0.9,
        "attribution": {"provider": "courtlistener"},
        "fetched_at": "2026-10-16T00:00:00+00:00",
        "raw_payload": {"kind": kind, "match": match, "party_name": party, **extra},
    }


def _report():
    return Report(
        id="report-1",
        verification_run_id="run-1",
        status="complete",
        section_statuses={"evidence": "complete"},
        summary={
            "evidence": [
                _item(
                    "bankruptcy",
                    "strong",
                    "Contoso Freight LLC",
                    docket_number="25-10417",
                ),
                _item(
                    "federal_exclusion",
                    "weak",
                    "Jordan Example",
                    classification="Individual",
                ),
                {
                    "id": "ev-tax",
                    "source": "tax_id",
                    "tier": 1,
                    "field": "tax_id_status",
                    "raw_value": "verified",
                    "normalized_value": "verified",
                },
            ],
            "sources": [],
            "mismatches": [],
        },
    )


def _payloads(response):
    return {e.id: e.raw_payload for e in response.evidence}


def test_operator_sees_public_records_payload():
    payloads = _payloads(_serialize_report(_report(), viewer="operator"))
    assert payloads["ev-bankruptcy-strong"]["docket_number"] == "25-10417"
    assert payloads["ev-federal_exclusion-weak"]["party_name"] == "Jordan Example"
    assert payloads["ev-tax"] is None  # only public-records rows carry a payload


def test_api_key_gets_person_names_redacted():
    payloads = _payloads(_serialize_report(_report(), viewer="system"))
    assert payloads["ev-bankruptcy-strong"]["party_name"] == "Contoso Freight LLC"
    assert payloads["ev-federal_exclusion-weak"]["party_name"] == REDACTED
