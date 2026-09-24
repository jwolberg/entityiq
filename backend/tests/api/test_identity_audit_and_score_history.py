"""Ticket 0019 (IC4-T2) — audit + score-change history for identity signals.

Verifies, against the REAL pipeline (StubTaxIdProvider / StubLinkedInProvider
at the network edge only, same pattern as tests/pipeline/test_pipeline_e2e.py
and tests/api/test_reports_identity.py — no hand-built RiskAssessment rows):

  1. The report's sources list (PRD § Auditability Requirements: "Evidence
     sources used"; PRD-identity-corroboration § 11: "Both adapters are
     sources used within a run and are recorded as such") lists tax_id and
     linkedin once their stages produce evidence.
  2. Correcting a submission's tax_id and re-analyzing records a score change
     attributable to a *new* tax_id signal — not just "some number moved".
  3. No audit_event row already on record is ever mutated by a later
     correction/re-analysis (append-only, ARCHITECTURE § 6).
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — registers ORM models with Base.metadata
from app.api.reports import _get_db as reports_get_db
from app.api.workflow import _get_db as workflow_get_db
from app.audit.recorder import record_event
from app.auth.operator import _clear_all_sessions, hash_password
from app.auth.operator import _get_db as auth_get_db
from app.auth.service import _get_db as service_get_db
from app.db.session import Base
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.operator import Operator
from app.models.risk_assessment import RiskAssessment
from app.pipeline.orchestrator import Orchestrator
from tests.pipeline.test_pipeline_e2e import _ACME_REGISTRY, _stages, _submit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pipeline_stages():
    """Real stage list, stub tax-ID/LinkedIn providers (no network)."""
    return _stages(
        age_days=4000,
        mx=["aspmx.l.google.com"],
        txt=["v=spf1 include:_spf.google.com ~all"],
        registry=_ACME_REGISTRY,
    )


@pytest.fixture
def audit_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit_identity.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def audit_client(audit_engine):
    """TestClient wired to audit_engine, with enqueue_run patched to run the
    REAL pipeline synchronously in-process (P1-T2's run_sync() pattern —
    no Celery/Redis needed) instead of dispatching to Celery.
    """
    session_factory = sessionmaker(bind=audit_engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[workflow_get_db] = override_get_db
    app.dependency_overrides[reports_get_db] = override_get_db
    app.dependency_overrides[auth_get_db] = override_get_db
    app.dependency_overrides[service_get_db] = override_get_db

    def _run_synchronously(run_id: str) -> None:
        sess = session_factory()
        try:
            Orchestrator(_pipeline_stages()).run_sync(run_id, sess)
        finally:
            sess.close()

    with mock.patch(
        "app.pipeline.orchestrator.enqueue_run", side_effect=_run_synchronously
    ):
        client = TestClient(app, raise_server_exceptions=True)
        yield client, session_factory

    app.dependency_overrides.pop(workflow_get_db, None)
    app.dependency_overrides.pop(reports_get_db, None)
    app.dependency_overrides.pop(auth_get_db, None)
    app.dependency_overrides.pop(service_get_db, None)
    _clear_all_sessions()


def _sign_in(client: TestClient, session_factory, email: str) -> str:
    db = session_factory()
    try:
        op = Operator(
            email=email,
            full_name="Test Operator",
            role="operator",
            password_hash=hash_password("pw"),
        )
        db.add(op)
        db.commit()
    finally:
        db.close()
    resp = client.post("/auth/sign-in", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["session_token"]


def _seed_prior_run(session_factory, *, tax_id: str) -> tuple[str, str]:
    """Submit + run the real pipeline; record the submission-received audit
    event production code always writes (app.demo_data / submissions router
    both call record_event() the same way). Returns (run_id, submission_id).
    """
    db = session_factory()
    try:
        run = _submit(db, tax_id=tax_id)
        record_event(
            db,
            "system.submission_received",
            submission_id=run.submission_id,
            verification_run_id=run.id,
            payload={"system": "test-harness", "endpoint": "/submissions"},
            description="Test harness submitted registration for Acme Corporation.",
        )
        Orchestrator(_pipeline_stages()).run_sync(run.id, db)
        return run.id, run.submission_id
    finally:
        db.close()


def _signal_names(session_factory, run_id: str) -> set[str]:
    db = session_factory()
    try:
        ra = (
            db.query(RiskAssessment)
            .filter(RiskAssessment.verification_run_id == run_id)
            .one()
        )
        return {s["name"] for s in (ra.contributing_signals or [])}
    finally:
        db.close()


def _overall_score(session_factory, run_id: str) -> float:
    db = session_factory()
    try:
        ra = (
            db.query(RiskAssessment)
            .filter(RiskAssessment.verification_run_id == run_id)
            .one()
        )
        return ra.overall_score
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. Sources-used audit trail
# ---------------------------------------------------------------------------


def test_sources_used_audit_lists_tax_id_and_linkedin(audit_client):
    client, session_factory = audit_client
    run_id, _ = _seed_prior_run(session_factory, tax_id="12-3456789")

    token = _sign_in(client, session_factory, "auditor@test.example")
    resp = client.get(
        f"/reports/{run_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text

    sources = {s["source"]: s for s in resp.json()["sources"]}
    assert "tax_id" in sources, sources
    assert sources["tax_id"]["status"] == "available"
    assert sources["tax_id"]["evidence_count"] > 0

    assert "linkedin" in sources, sources
    assert sources["linkedin"]["status"] == "available"
    assert sources["linkedin"]["evidence_count"] > 0


# ---------------------------------------------------------------------------
# 2. Score-change history attributable to a new signal
# ---------------------------------------------------------------------------


def test_reanalysis_after_correction_records_score_change_from_new_signal(
    audit_client,
):
    client, session_factory = audit_client

    # An unknown FEIN ("00-0000000" isn't in StubTaxIdProvider's records) is
    # a finding: tax_id_not_found (elevated).
    prior_run_id, _ = _seed_prior_run(session_factory, tax_id="00-0000000")
    prior_signals = _signal_names(session_factory, prior_run_id)
    prior_score = _overall_score(session_factory, prior_run_id)
    assert "tax_id_not_found" in prior_signals
    assert "tax_id_verified_active" not in prior_signals

    token = _sign_in(client, session_factory, "corrector@test.example")
    resp = client.post(
        f"/workflow/runs/{prior_run_id}/correct",
        json={"corrections": {"tax_id": "12-3456789"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202, resp.text
    new_run_id = resp.json()["new_run_id"]

    new_signals = _signal_names(session_factory, new_run_id)
    new_score = _overall_score(session_factory, new_run_id)

    # The corrected FEIN resolves and matches the submitted company name
    # ("Acme Corporation" in StubTaxIdProvider's default records) — a new
    # trust signal replaces the prior elevated one.
    assert "tax_id_verified_active" in new_signals
    assert "tax_id_not_found" not in new_signals

    # The score actually moved, and it's attributable to that signal swap —
    # not a coincidental change elsewhere (every other input is identical:
    # same registry, domain, IP, web, LinkedIn fixtures).
    assert new_score != prior_score


# ---------------------------------------------------------------------------
# 3. Append-only audit log
# ---------------------------------------------------------------------------


def test_correction_and_reanalysis_never_mutates_prior_audit_rows(audit_client):
    client, session_factory = audit_client
    prior_run_id, submission_id = _seed_prior_run(session_factory, tax_id="00-0000000")

    db = session_factory()
    try:
        before_rows = {
            e.id: (
                e.event_type,
                e.operator_id,
                e.api_client_id,
                e.verification_run_id,
                e.submission_id,
                e.payload,
                e.description,
                e.occurred_at,
            )
            for e in db.query(AuditEvent).all()
        }
    finally:
        db.close()
    assert before_rows, "expected at least the seeded submission-received event"

    token = _sign_in(client, session_factory, "append-only@test.example")
    resp = client.post(
        f"/workflow/runs/{prior_run_id}/correct",
        json={"corrections": {"tax_id": "12-3456789"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202, resp.text

    db = session_factory()
    try:
        after_rows = {
            e.id: (
                e.event_type,
                e.operator_id,
                e.api_client_id,
                e.verification_run_id,
                e.submission_id,
                e.payload,
                e.description,
                e.occurred_at,
            )
            for e in db.query(AuditEvent).all()
        }
    finally:
        db.close()

    # Every row that existed before the correction still exists, byte-for-byte
    # identical — no UPDATE, and none were deleted to make room for new ones.
    for event_id, snapshot in before_rows.items():
        assert event_id in after_rows, f"audit row {event_id} disappeared"
        assert after_rows[event_id] == snapshot, f"audit row {event_id} mutated"

    # New events were appended (sign-in + correct_and_rerun), not merged into
    # existing rows.
    assert len(after_rows) > len(before_rows)
    new_ids = set(after_rows) - set(before_rows)
    correction_events = [
        after_rows[i]
        for i in new_ids
        if after_rows[i][0] == "operator.correct_and_rerun"
    ]
    assert len(correction_events) == 1
    assert correction_events[0][4] == submission_id
