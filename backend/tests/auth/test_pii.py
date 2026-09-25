"""Tests for app/auth/pii.py — role-gated report access (ADR-0002 §2; 0002, 0020).

Unit-level: exercises resolve_viewer(), filter_summary_for_viewer(), and
report_contains_pii() directly against summary dict shapes matching
app.scoring.report._build_summary() — the same shape stored on Report.summary.
Integration-level API tests live in tests/api/test_reports_pii.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.auth.pii import (
    filter_summary_for_viewer,
    report_contains_pii,
    resolve_viewer,
)
from app.auth.service import Principal
from app.db.session import Base
from app.models.operator import Operator


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    sess = Session(bind=engine)
    yield sess
    sess.close()


def _summary() -> dict:
    return {
        "scores": {"overall_score": 72.0, "triage_tier": "review"},
        "evidence": [
            {
                "id": "ev-1",
                "source": "opencorporates",
                "tier": 1,
                "field": "company_name",
                "raw_value": "Acme Corp",
                "normalized_value": "Acme Corp",
                "confidence": 0.9,
                "attribution": {"provider": "opencorporates"},
                "fetched_at": None,
            },
            {
                "id": "ev-2",
                "source": "ipinfo",
                "tier": 2,
                "field": "ip_country",
                "raw_value": "US",
                "normalized_value": "US",
                "confidence": 0.9,
                "attribution": {
                    "provider": "ipinfo",
                    "source_url": "https://ipinfo.io/73.162.40.18/json",
                },
                "fetched_at": None,
            },
            {
                "id": "ev-3",
                "source": "linkedin",
                "tier": 3,
                "field": "linkedin_requester_match",
                "raw_value": "true",
                "normalized_value": "true",
                "confidence": 0.6,
                "attribution": {"provider": "stub"},
                "fetched_at": None,
            },
            {
                "id": "ev-4",
                "source": "linkedin",
                "tier": 3,
                "field": "linkedin_presence",
                "raw_value": "found",
                "normalized_value": "found",
                "confidence": 0.7,
                "attribution": {"provider": "stub"},
                "fetched_at": None,
            },
        ],
        "mismatches": [
            {
                "id": "fc-1",
                "field_name": "company_name",
                "submitted_value": "Acme Corp",
                "discovered_value": "Acme Corp",
                "match_status": "match",
                "evidence_id": "ev-1",
            },
            {
                "id": "fc-2",
                "field_name": "billing_address",
                "submitted_value": "123 Main St",
                "discovered_value": None,
                "match_status": "unverified",
                "evidence_id": None,
            },
        ],
        "sources": [
            {
                "source": "opencorporates",
                "tier": 1,
                "evidence_count": 1,
                "attribution": {"provider": "opencorporates"},
                "status": "available",
            },
            {
                "source": "ipinfo",
                "tier": 2,
                "evidence_count": 1,
                "attribution": {
                    "provider": "ipinfo",
                    "source_url": "https://ipinfo.io/73.162.40.18/json",
                },
                "status": "available",
            },
        ],
    }


# ---------------------------------------------------------------------------
# resolve_viewer
# ---------------------------------------------------------------------------


def test_system_principal_resolves_to_system_viewer(db):
    principal = Principal(
        kind="system", id="cl-1", name="acme-integration", api_client_id="cl-1"
    )
    assert resolve_viewer(principal, db) == "system"


def test_lead_operator_resolves_to_lead_viewer(db):
    lead = Operator(email="lead@example.com", full_name="Lead", role="lead")
    db.add(lead)
    db.commit()
    principal = Principal(
        kind="operator", id=lead.id, name=lead.email, operator_id=lead.id
    )
    assert resolve_viewer(principal, db) == "lead"


def test_operator_role_resolves_to_operator_viewer(db):
    op = Operator(email="op@example.com", full_name="Op", role="operator")
    db.add(op)
    db.commit()
    principal = Principal(kind="operator", id=op.id, name=op.email, operator_id=op.id)
    assert resolve_viewer(principal, db) == "operator"


def test_unknown_operator_id_defaults_to_operator_viewer(db):
    """Fail-safe: an operator row that can't be resolved never grants lead access."""
    principal = Principal(
        kind="operator", id="ghost", name="ghost@example.com", operator_id="ghost"
    )
    assert resolve_viewer(principal, db) == "operator"


# ---------------------------------------------------------------------------
# filter_summary_for_viewer
# ---------------------------------------------------------------------------


def test_lead_viewer_sees_everything_unredacted():
    filtered = filter_summary_for_viewer(_summary(), "lead")
    fields = {(e["source"], e["field"]) for e in filtered["evidence"]}
    assert ("ipinfo", "ip_country") in fields
    assert ("linkedin", "linkedin_requester_match") in fields
    ipinfo_ev = next(e for e in filtered["evidence"] if e["source"] == "ipinfo")
    assert (
        ipinfo_ev["attribution"]["source_url"] == "https://ipinfo.io/73.162.40.18/json"
    )
    field_names = {m["field_name"] for m in filtered["mismatches"]}
    assert "billing_address" in field_names


def test_operator_viewer_keeps_derived_network_signals_but_strips_raw_ip():
    filtered = filter_summary_for_viewer(_summary(), "operator")
    ipinfo_ev = next(e for e in filtered["evidence"] if e["source"] == "ipinfo")
    assert ipinfo_ev["field"] == "ip_country"  # derived signal still present
    assert ipinfo_ev["normalized_value"] == "US"
    assert "source_url" not in ipinfo_ev["attribution"]
    assert ipinfo_ev["attribution"]["provider"] == "ipinfo"

    ipinfo_src = next(s for s in filtered["sources"] if s["source"] == "ipinfo")
    assert "source_url" not in ipinfo_src["attribution"]


def test_operator_viewer_keeps_requester_association_evidence():
    filtered = filter_summary_for_viewer(_summary(), "operator")
    fields = {(e["source"], e["field"]) for e in filtered["evidence"]}
    assert ("linkedin", "linkedin_requester_match") in fields


def test_operator_viewer_keeps_billing_address_mismatch():
    filtered = filter_summary_for_viewer(_summary(), "operator")
    field_names = {m["field_name"] for m in filtered["mismatches"]}
    assert "billing_address" in field_names


def test_system_viewer_excludes_network_metadata_entirely():
    filtered = filter_summary_for_viewer(_summary(), "system")
    sources_used = {e["source"] for e in filtered["evidence"]}
    assert "ipinfo" not in sources_used
    assert not any(s["source"] == "ipinfo" for s in filtered["sources"])


def test_system_viewer_excludes_requester_association_evidence():
    filtered = filter_summary_for_viewer(_summary(), "system")
    fields = {e["field"] for e in filtered["evidence"]}
    assert "linkedin_requester_match" not in fields
    # Company-level LinkedIn evidence (presence) is still company-level data.
    assert "linkedin_presence" in fields


def test_system_viewer_excludes_contact_pii_mismatches():
    filtered = filter_summary_for_viewer(_summary(), "system")
    field_names = {m["field_name"] for m in filtered["mismatches"]}
    assert "billing_address" not in field_names
    assert "company_name" in field_names  # company-level comparison retained


def test_system_viewer_keeps_company_level_evidence_and_scores():
    filtered = filter_summary_for_viewer(_summary(), "system")
    fields = {e["field"] for e in filtered["evidence"]}
    assert "company_name" in fields
    assert filtered["scores"] == _summary()["scores"]


def test_filtering_does_not_mutate_the_input_summary():
    original = _summary()
    import copy

    snapshot = copy.deepcopy(original)
    filter_summary_for_viewer(original, "system")
    assert original == snapshot


# ---------------------------------------------------------------------------
# report_contains_pii
# ---------------------------------------------------------------------------


def test_report_contains_pii_true_for_network_metadata():
    assert report_contains_pii(_summary()) is True


def test_report_contains_pii_false_for_company_only_summary():
    summary = {
        "evidence": [
            {"id": "ev-1", "source": "opencorporates", "field": "company_name"}
        ],
        "mismatches": [
            {"id": "fc-1", "field_name": "company_name"},
        ],
    }
    assert report_contains_pii(summary) is False
