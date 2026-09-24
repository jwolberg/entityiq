"""Integration tests: role-gated PII access on the report API (ADR-0002 §2,
ticket 0002).

Builds a run with ipinfo (network metadata) and a billing_address mismatch,
then reads GET /reports/{run_id} and GET /reports/{run_id}/export as a lead,
an operator, and an integration API key — asserting each sees exactly what
ADR-0002 §2 allows.

Also covers: viewing a report with PII records an audit event
(app.auth.operator.require_lead's denial-audit is covered separately in
tests/auth/test_operator.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.reports import _get_db
from app.auth.service import Principal, get_principal
from app.db.session import Base
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.field_comparison import FieldComparison
from app.models.operator import Operator
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.scoring.report import assemble_report


@pytest.fixture
def pii_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def SessionMaker(pii_engine):
    return sessionmaker(bind=pii_engine, autocommit=False, autoflush=False)


@pytest.fixture(autouse=True)
def _cleanup_dependency_overrides():
    """`_client_as()` below sets dependency_overrides per-test without its own
    fixture teardown (it's called directly, not yielded) — clear them here so
    a Principal/db override never leaks into a later test module (app is a
    process-wide singleton; app.dependency_overrides is shared across every
    test file in the run)."""
    yield
    app.dependency_overrides.pop(_get_db, None)
    app.dependency_overrides.pop(get_principal, None)


def _client_as(pii_engine, principal: Principal) -> TestClient:
    factory = sessionmaker(bind=pii_engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[_get_db] = override_get_db
    app.dependency_overrides[get_principal] = lambda: principal
    client = TestClient(app, raise_server_exceptions=True)
    return client


def _build_run(SessionMaker) -> str:
    db: Session = SessionMaker()
    try:
        entity = Entity(canonical_name="PII Corp", canonical_domain="pii.example")
        db.add(entity)
        db.flush()

        sub = Submission(
            company_name="PII Corp",
            domain="pii.example",
            work_email="ceo@pii.example",
            country="US",
            billing_address="123 Main St",
            requester_full_name="Jane Smith",
            entity_id=entity.id,
        )
        db.add(sub)
        db.flush()

        run = VerificationRun(
            submission_id=sub.id,
            entity_id=entity.id,
            status="complete",
            source_availability={
                "query_registries": "complete",
                "enrich_network_ip": "complete",
                "consistency_checks": "complete",
                "scoring": "complete",
            },
        )
        db.add(run)
        db.flush()
        run_id = run.id

        db.add_all(
            [
                Evidence(
                    verification_run_id=run_id,
                    source="opencorporates",
                    tier=1,
                    field="company_name",
                    raw_value="PII Corp",
                    normalized_value="PII Corp",
                    confidence=0.9,
                    attribution={"provider": "opencorporates"},
                    fetched_at=datetime.now(tz=timezone.utc),
                ),
                Evidence(
                    verification_run_id=run_id,
                    source="ipinfo",
                    tier=2,
                    field="ip_country",
                    raw_value="US",
                    normalized_value="US",
                    confidence=0.9,
                    attribution={
                        "provider": "ipinfo",
                        "source_url": "https://ipinfo.io/73.162.40.18/json",
                    },
                    fetched_at=datetime.now(tz=timezone.utc),
                ),
            ]
        )
        db.add(
            FieldComparison(
                verification_run_id=run_id,
                field_name="billing_address",
                submitted_value="123 Main St",
                discovered_value=None,
                match_status="unverified",
            )
        )
        db.commit()

        assemble_report(run_id, db)
    finally:
        db.close()
    return run_id


@pytest.fixture(params=["/reports/{id}", "/reports/{id}/export"])
def report_path(request):
    return request.param


# ---------------------------------------------------------------------------
# Lead: full access
# ---------------------------------------------------------------------------


def test_lead_sees_raw_network_attribution(pii_engine, SessionMaker, report_path):
    lead = Operator(email="lead@example.com", full_name="Lead", role="lead")
    db = SessionMaker()
    db.add(lead)
    db.commit()
    lead_id = lead.id
    db.close()

    run_id = _build_run(SessionMaker)
    client = _client_as(
        pii_engine,
        Principal(
            kind="operator", id=lead_id, name="lead@example.com", operator_id=lead_id
        ),
    )
    data = client.get(report_path.format(id=run_id)).json()

    ipinfo_ev = next(e for e in data["evidence"] if e["source"] == "ipinfo")
    assert (
        ipinfo_ev["attribution"]["source_url"] == "https://ipinfo.io/73.162.40.18/json"
    )
    mismatch_fields = {m["field_name"] for m in data["mismatches"]}
    assert "billing_address" in mismatch_fields


# ---------------------------------------------------------------------------
# Operator: derived signals only, no raw IP
# ---------------------------------------------------------------------------


def test_operator_sees_derived_signals_not_raw_ip(
    pii_engine, SessionMaker, report_path
):
    op = Operator(email="op@example.com", full_name="Op", role="operator")
    db = SessionMaker()
    db.add(op)
    db.commit()
    op_id = op.id
    db.close()

    run_id = _build_run(SessionMaker)
    client = _client_as(
        pii_engine,
        Principal(kind="operator", id=op_id, name="op@example.com", operator_id=op_id),
    )
    data = client.get(report_path.format(id=run_id)).json()

    ipinfo_ev = next(e for e in data["evidence"] if e["source"] == "ipinfo")
    assert ipinfo_ev["field"] == "ip_country"
    assert "source_url" not in ipinfo_ev["attribution"]

    mismatch_fields = {m["field_name"] for m in data["mismatches"]}
    assert "billing_address" in mismatch_fields  # operators still read submitted PII


# ---------------------------------------------------------------------------
# Integration API key: no network metadata, no contact-PII mismatches
# ---------------------------------------------------------------------------


def test_api_key_excluded_from_network_metadata_and_contact_pii(
    pii_engine, SessionMaker, report_path
):
    run_id = _build_run(SessionMaker)
    client = _client_as(
        pii_engine,
        Principal(
            kind="system", id="cl-1", name="acme-integration", api_client_id="cl-1"
        ),
    )
    data = client.get(report_path.format(id=run_id)).json()

    sources_used = {e["source"] for e in data["evidence"]}
    assert "ipinfo" not in sources_used
    assert not any(s["source"] == "ipinfo" for s in data["sources"])

    mismatch_fields = {m["field_name"] for m in data["mismatches"]}
    assert "billing_address" not in mismatch_fields

    # Company-level evidence is still returned; scores (when present) are
    # never redacted — they're an aggregate, not raw PII.
    fields = {e["field"] for e in data["evidence"]}
    assert "company_name" in fields


# ---------------------------------------------------------------------------
# Viewing a report with PII is audited (GET /reports/{run_id} only)
# ---------------------------------------------------------------------------


def test_viewing_report_with_pii_records_audit_event(pii_engine, SessionMaker):
    run_id = _build_run(SessionMaker)
    client = _client_as(
        pii_engine,
        Principal(
            kind="operator", id="op-1", name="op@example.com", operator_id="op-1"
        ),
    )
    resp = client.get(f"/reports/{run_id}")
    assert resp.status_code == 200

    db = SessionMaker()
    try:
        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "report.viewed")
            .filter(AuditEvent.verification_run_id == run_id)
            .all()
        )
        assert len(events) == 1
        assert events[0].operator_id == "op-1"
    finally:
        db.close()


def test_report_without_pii_does_not_record_view_audit_event(pii_engine, SessionMaker):
    db: Session = SessionMaker()
    try:
        entity = Entity(canonical_name="Clean Corp", canonical_domain="clean.example")
        db.add(entity)
        db.flush()
        sub = Submission(
            company_name="Clean Corp",
            domain="clean.example",
            work_email="ceo@clean.example",
            country="US",
            entity_id=entity.id,
        )
        db.add(sub)
        db.flush()
        run = VerificationRun(
            submission_id=sub.id,
            entity_id=entity.id,
            status="complete",
            source_availability={"query_registries": "complete", "scoring": "complete"},
        )
        db.add(run)
        db.flush()
        run_id = run.id
        db.add(
            Evidence(
                verification_run_id=run_id,
                source="opencorporates",
                tier=1,
                field="company_name",
                raw_value="Clean Corp",
                normalized_value="Clean Corp",
                confidence=0.9,
                fetched_at=datetime.now(tz=timezone.utc),
            )
        )
        db.commit()
        assemble_report(run_id, db)
    finally:
        db.close()

    client = _client_as(
        pii_engine,
        Principal(
            kind="operator", id="op-1", name="op@example.com", operator_id="op-1"
        ),
    )
    client.get(f"/reports/{run_id}")

    db = SessionMaker()
    try:
        events = (
            db.query(AuditEvent).filter(AuditEvent.event_type == "report.viewed").all()
        )
        assert events == []
    finally:
        db.close()
