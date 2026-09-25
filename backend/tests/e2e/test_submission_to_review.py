"""Cross-layer e2e test (ticket 0004 / plan U25 / P3-T5).

Drives the real HTTP boundary end to end:

    POST /submissions  →  real pipeline (recorded fakes, no network)
                        →  GET /reports/{run_id}
                        →  operator sign-in
                        →  POST /reviews/{run_id}
                        →  GET /reports/{run_id} (reviewed)

This intentionally does NOT re-test pipeline internals — that is
tests/pipeline/test_pipeline_e2e.py's job (stage-by-stage signal assertions).
This test instead proves the API → pipeline → report → operator-review
boundary is wired correctly: a submission accepted over HTTP is analyzed and
becomes a report an operator can retrieve and mark reviewed over HTTP, with
an audit trail attributing both the ingest and the review.

Uses a demo scenario (app.demo_data) for its fields and recorded-response
stage list, so this test needs no live network and stays consistent with the
demo dataset's fixtures (CLAUDE.md §12.2: "Reuse the recorded source
responses from app/demo_data.py").
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all ORM models with Base.metadata
from app.api.reports import _get_db as reports_get_db
from app.api.reviews import _get_db as reviews_get_db
from app.api.submissions import _get_db as submissions_get_db
from app.auth.operator import _clear_all_sessions, hash_password
from app.auth.operator import _get_db as auth_get_db
from app.auth.service import _get_db as service_get_db
from app.db.session import Base
from app.demo_data import SCENARIOS, _stages
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.operator import Operator
from app.models.verification_run import VerificationRun
from app.pipeline.orchestrator import Orchestrator

# Northwind Traders: established distributor, recorded fixtures resolve to
# triage_tier "pre_clear" (see app.demo_data.SCENARIOS[0]).
_SCENARIO = SCENARIOS[0]
assert _SCENARIO.expected_tier == "pre_clear"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e2e_engine():
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
def db(e2e_engine):
    """Transactional session used by the test to set up/inspect rows directly."""
    connection = e2e_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(e2e_engine, service_credential):
    """TestClient wired to the shared SQLite engine, with enqueue_run stubbed.

    The submission endpoint's real job (persist submission + run, audit the
    ingest) runs for real; only the Celery hand-off is stubbed, matching the
    project's Celery-testability pattern (run_sync() is called directly by
    this test to drive the pipeline instead).
    """
    TestingSessionLocal = sessionmaker(
        bind=e2e_engine, autocommit=False, autoflush=False
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    overrides = {
        submissions_get_db: override_get_db,
        reports_get_db: override_get_db,
        reviews_get_db: override_get_db,
        auth_get_db: override_get_db,
        service_get_db: override_get_db,
    }
    for dep, override in overrides.items():
        app.dependency_overrides[dep] = override

    with mock.patch("app.pipeline.orchestrator.enqueue_run") as _mock_enqueue:
        _mock_enqueue.return_value = None
        c = TestClient(
            app,
            raise_server_exceptions=True,
            headers={"X-API-Key": service_credential["key"]},
        )
        yield c

    for dep in overrides:
        app.dependency_overrides.pop(dep, None)
    _clear_all_sessions()


@pytest.fixture(scope="module")
def service_credential(e2e_engine):
    """Seed one integration API key, shared by every test in this module."""
    from app.auth.service import create_api_client

    SessionMaker = sessionmaker(bind=e2e_engine, autocommit=False, autoflush=False)
    sess = SessionMaker()
    try:
        client, key = create_api_client("e2e-test-system", sess)
        info = {"id": client.id, "key": key}
    finally:
        sess.close()
    return info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _submission_payload() -> dict:
    s = _SCENARIO
    return {
        "company_name": s.company_name,
        "work_email": s.work_email,
        "company_domain": s.domain,
        "country": s.country,
        "billing_address": s.billing_address,
        "requester_full_name": s.requester_full_name,
    }


def _sign_in_operator(client: TestClient, db: Session, email: str) -> str:
    op = Operator(
        email=email,
        full_name="E2E Operator",
        role="operator",
        password_hash=hash_password("e2e-pw"),
    )
    db.add(op)
    db.commit()
    resp = client.post("/auth/sign-in", json={"email": email, "password": "e2e-pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["session_token"]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_submit_to_reviewed_report_end_to_end(client, db, e2e_engine):
    # --- 1. Submit over HTTP (API-key authenticated integrating system) ---
    resp = client.post("/submissions", json=_submission_payload())
    assert resp.status_code == 202, resp.text
    body = resp.json()
    run_id = body["run_id"]
    assert body["status"] == "pending"

    # A report doesn't exist yet — the pipeline hasn't run.
    resp = client.get(f"/reports/{run_id}")
    assert resp.status_code == 404, resp.text

    # --- 2. Run the real pipeline, network clients replaced by recorded
    # fixtures (no live network calls) — same session the API used. ---
    run_db = sessionmaker(bind=e2e_engine, autocommit=False, autoflush=False)()
    try:
        Orchestrator(_stages(_SCENARIO)).run_sync(run_id, run_db)
    finally:
        run_db.close()

    # --- 3. Retrieve the report over HTTP ---
    resp = client.get(f"/reports/{run_id}")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["status"] == "complete"
    assert report["section_statuses"] == {
        "scores": "complete",
        "evidence": "complete",
        "mismatches": "complete",
        "sources": "complete",
    }
    assert report["scores"]["triage_tier"] == "pre_clear"
    assert report["scores"]["overall_score"] is not None
    assert len(report["evidence"]) > 0
    assert len(report["sources"]) > 0
    assert report["review"] is None  # not yet reviewed
    assert report["run"]["status"] == "complete"

    # --- 4. Operator signs in and marks the run reviewed ---
    token = _sign_in_operator(client, db, "e2e-operator@demo.entityiq.dev")
    resp = client.post(
        f"/reviews/{run_id}",
        json={
            "notes": "Looks good — established distributor.",
            "review_status": "approved",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    review_body = resp.json()
    assert review_body["run_id"] == run_id
    assert review_body["review_status"] == "approved"

    # Unauthenticated review attempts are rejected — reviewing is operator-only.
    resp = client.post(f"/reviews/{run_id}", json={"review_status": "approved"})
    assert resp.status_code == 401, resp.text

    # --- 5. The report now carries the review decision ---
    resp = client.get(f"/reports/{run_id}")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["review"] is not None
    assert report["review"]["status"] == "approved"
    assert report["review"]["reviewer_name"] == "E2E Operator"
    assert report["review"]["notes"] == "Looks good — established distributor."

    # --- 6. Every step left an attributable audit trail ---
    run = db.get(VerificationRun, run_id)
    assert run is not None
    events = (
        db.query(AuditEvent)
        .filter(
            (AuditEvent.verification_run_id == run_id)
            | (AuditEvent.submission_id == run.submission_id)
        )
        .all()
    )
    event_types = {e.event_type for e in events}
    assert "system.submission_received" in event_types
    assert "operator.mark_reviewed" in event_types
