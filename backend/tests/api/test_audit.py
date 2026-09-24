"""P3-T2 — audit trail is readable: per-run timeline (any operator) and the
global log (lead only).  Before this, events were written but no API or UI
could read them back."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.audit import _get_db as audit_get_db
from app.audit.recorder import record_event
from app.auth.operator import _clear_all_sessions, _new_session_token
from app.auth.operator import _get_db as operator_get_db
from app.main import app
from app.models.entity import Entity
from app.models.operator import Operator
from app.models.submission import Submission
from app.models.verification_run import VerificationRun


@pytest.fixture
def Maker(sqlite_engine):
    return sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)


@pytest.fixture
def client(Maker):
    def override():
        db = Maker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[audit_get_db] = override
    app.dependency_overrides[operator_get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
    _clear_all_sessions()


def _operator(Maker, role: str) -> tuple[str, str]:
    db = Maker()
    try:
        op = Operator(
            email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
            full_name=f"Test {role.title()}",
            role=role,
        )
        db.add(op)
        db.commit()
        return op.id, _new_session_token(op.id)
    finally:
        db.close()


def _run_with_events(Maker, operator_id: str) -> str:
    db = Maker()
    try:
        entity = Entity(canonical_name="Audit Co", canonical_domain="audit.example")
        db.add(entity)
        db.flush()
        sub = Submission(
            company_name="Audit Co",
            domain="audit.example",
            work_email="a@audit.example",
            country="US",
            entity_id=entity.id,
        )
        db.add(sub)
        db.flush()
        run = VerificationRun(submission_id=sub.id, entity_id=entity.id)
        db.add(run)
        db.commit()
        record_event(
            db,
            "system.submission_received",
            submission_id=sub.id,
            verification_run_id=run.id,
            description="submitted",
        )
        record_event(
            db,
            "operator.mark_reviewed",
            operator_id=operator_id,
            verification_run_id=run.id,
            description="reviewed",
        )
        record_event(db, "operator.sign_in", operator_id=operator_id)  # other run
        return run.id
    finally:
        db.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_run_timeline_requires_sign_in(client):
    assert client.get("/audit/runs/any").status_code == 401


def test_operator_sees_run_timeline_oldest_first_with_actor(client, Maker):
    op_id, token = _operator(Maker, "operator")
    run_id = _run_with_events(Maker, op_id)

    resp = client.get(f"/audit/runs/{run_id}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    events = resp.json()["events"]
    assert [e["event_type"] for e in events] == [
        "system.submission_received",
        "operator.mark_reviewed",
    ]
    assert events[1]["actor"].startswith("operator-")


def test_global_log_is_lead_only(client, Maker):
    _, op_token = _operator(Maker, "operator")
    assert client.get("/audit/events", headers=_auth(op_token)).status_code == 403


def test_lead_sees_global_log_newest_first(client, Maker):
    lead_id, token = _operator(Maker, "lead")
    _run_with_events(Maker, lead_id)

    resp = client.get("/audit/events?limit=2", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    events = resp.json()["events"]
    assert len(events) == 2
    assert events[0]["occurred_at"] >= events[1]["occurred_at"]
