"""Tests for P2-T11 — Re-analysis endpoint.

POST /reanalysis/{run_id}

Scenarios:
  1. Authenticated operator triggers re-analysis → new run supersedes prior
  2. Re-analysis audit event is recorded
  3. Unauthenticated request → 401
  4. Unknown run_id → 404
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.reanalysis import _get_db as reanalysis_get_db
from app.auth.operator import (
    _clear_all_sessions,
    hash_password,
)
from app.auth.operator import (
    _get_db as auth_get_db,
)
from app.db.session import Base
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.entity import Entity
from app.models.operator import Operator
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reanalysis_engine():
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
def db(reanalysis_engine):
    connection = reanalysis_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(reanalysis_engine):
    TestingSessionLocal = sessionmaker(
        bind=reanalysis_engine, autocommit=False, autoflush=False
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[reanalysis_get_db] = override_get_db
    app.dependency_overrides[auth_get_db] = override_get_db

    with mock.patch("app.pipeline.orchestrator.enqueue_run") as _mock_enqueue:
        _mock_enqueue.return_value = None
        c = TestClient(app, raise_server_exceptions=True)
        yield c

    app.dependency_overrides.pop(reanalysis_get_db, None)
    app.dependency_overrides.pop(auth_get_db, None)
    _clear_all_sessions()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_operator(db: Session, email: str = "op@acme.test") -> Operator:
    op = Operator(
        email=email,
        full_name="Test Op",
        role="operator",
        password_hash=hash_password("pw"),
    )
    db.add(op)
    db.commit()
    return op


def _make_run(db: Session) -> tuple[str, str]:
    """Return (run_id, entity_id)."""
    entity = Entity(canonical_name="Re Co", canonical_domain="re.example")
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Re Co",
        domain="re.example",
        work_email="ceo@re.example",
        country="US",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="complete",
    )
    db.add(run)
    db.commit()
    return run.id, entity.id


def _sign_in(client: TestClient, engine, email: str = "op@acme.test") -> str:
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    setup_db = TestingSessionLocal()
    try:
        _make_operator(setup_db, email=email)
    finally:
        setup_db.close()
    resp = client.post("/auth/sign-in", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["session_token"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReanalysis:
    def test_authenticated_triggers_reanalysis(self, client, reanalysis_engine):
        """Authenticated operator → 202 with new_run_id that supersedes prior."""
        TestingSessionLocal = sessionmaker(
            bind=reanalysis_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db)
        finally:
            setup_db.close()

        token = _sign_in(client, reanalysis_engine, "reanalyze_op@test.example")

        with mock.patch("app.pipeline.orchestrator.enqueue_run") as mock_enq:
            mock_enq.return_value = None
            resp = client.post(
                f"/reanalysis/{run_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["supersedes_run_id"] == run_id
        assert data["new_run_id"] != run_id
        assert data["status"] == "pending"
        assert "triggered_at" in data

    def test_new_run_supersedes_prior(self, client, reanalysis_engine):
        """New run created has supersedes_id pointing to the prior run."""
        TestingSessionLocal = sessionmaker(
            bind=reanalysis_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db)
        finally:
            setup_db.close()

        token = _sign_in(client, reanalysis_engine, "reanalyze_op2@test.example")

        with mock.patch("app.pipeline.orchestrator.enqueue_run") as mock_enq:
            mock_enq.return_value = None
            resp = client.post(
                f"/reanalysis/{run_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202, resp.text
        new_run_id = resp.json()["new_run_id"]

        # Verify the new run in DB
        check_db = TestingSessionLocal()
        try:
            new_run = check_db.get(VerificationRun, new_run_id)
            assert new_run is not None
            assert new_run.supersedes_id == run_id
        finally:
            check_db.close()

    def test_audit_event_recorded(self, client, reanalysis_engine):
        """Re-analysis trigger is recorded as audit event."""
        TestingSessionLocal = sessionmaker(
            bind=reanalysis_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db)
        finally:
            setup_db.close()

        token = _sign_in(client, reanalysis_engine, "reanalyze_op3@test.example")

        with mock.patch("app.pipeline.orchestrator.enqueue_run") as mock_enq:
            mock_enq.return_value = None
            resp = client.post(
                f"/reanalysis/{run_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202, resp.text
        new_run_id = resp.json()["new_run_id"]

        check_db = TestingSessionLocal()
        try:
            event = (
                check_db.query(AuditEvent)
                .filter(
                    AuditEvent.event_type == "operator.trigger_reanalysis",
                    AuditEvent.verification_run_id == new_run_id,
                )
                .first()
            )
            assert event is not None
            assert event.payload["supersedes_run_id"] == run_id
        finally:
            check_db.close()

    def test_unauthenticated_returns_401(self, client, reanalysis_engine):
        """No Authorization header → 401."""
        TestingSessionLocal = sessionmaker(
            bind=reanalysis_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db)
        finally:
            setup_db.close()

        resp = client.post(f"/reanalysis/{run_id}")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client, reanalysis_engine):
        """Bad Bearer token → 401."""
        TestingSessionLocal = sessionmaker(
            bind=reanalysis_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db)
        finally:
            setup_db.close()

        resp = client.post(
            f"/reanalysis/{run_id}",
            headers={"Authorization": "Bearer not-a-token"},
        )
        assert resp.status_code == 401

    def test_unknown_run_id_returns_404(self, client, reanalysis_engine):
        """Unknown run_id → 404."""
        token = _sign_in(client, reanalysis_engine, "reanalyze_op4@test.example")
        with mock.patch("app.pipeline.orchestrator.enqueue_run"):
            resp = client.post(
                "/reanalysis/00000000-0000-0000-0000-000000000000",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 404
