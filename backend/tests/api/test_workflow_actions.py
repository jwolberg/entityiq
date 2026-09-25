"""Tests for P2-T11 — Operator workflow endpoints.

  POST /workflow/runs/{run_id}/correct  — correct fields + re-run
  POST /workflow/runs/{run_id}/notes    — add review notes

Scenarios:
  Correct + re-run:
    1. Authenticated operator corrects field + triggers re-analysis
       → new run supersedes prior
    2. Corrections are recorded in audit event (before/after values)
    3. Invalid/non-correctable field name → 422
    4. Empty corrections dict → 422
    5. Unauthenticated → 401
    6. Unknown run_id → 404

  Add notes:
    7. Authenticated operator adds notes → review upserted + audit event
    8. Adding notes when review already exists appends notes
    9. Unauthenticated → 401

  Export report:
    10. GET /reports/{run_id}/export returns full normalized report
    11. Unknown run_id → 404 on export
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.reports import _get_db as reports_get_db
from app.api.workflow import _get_db as workflow_get_db
from app.auth.operator import (
    _clear_all_sessions,
    hash_password,
)
from app.auth.operator import (
    _get_db as auth_get_db,
)
from app.auth.service import _get_db as service_get_db
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
def wf_engine():
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
def db(wf_engine):
    connection = wf_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(wf_engine):
    TestingSessionLocal = sessionmaker(
        bind=wf_engine, autocommit=False, autoflush=False
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[workflow_get_db] = override_get_db
    app.dependency_overrides[reports_get_db] = override_get_db
    app.dependency_overrides[auth_get_db] = override_get_db
    app.dependency_overrides[service_get_db] = override_get_db

    with mock.patch("app.pipeline.orchestrator.enqueue_run") as _mock_enqueue:
        _mock_enqueue.return_value = None
        c = TestClient(app, raise_server_exceptions=True)
        yield c

    app.dependency_overrides.pop(workflow_get_db, None)
    app.dependency_overrides.pop(reports_get_db, None)
    app.dependency_overrides.pop(auth_get_db, None)
    app.dependency_overrides.pop(service_get_db, None)
    _clear_all_sessions()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_operator(db: Session, email: str) -> Operator:
    op = Operator(
        email=email,
        full_name="Test Op",
        role="operator",
        password_hash=hash_password("pw"),
    )
    db.add(op)
    db.commit()
    return op


def _make_run(db: Session, company: str = "Wf Co") -> tuple[str, str]:
    """Return (run_id, submission_id)."""
    slug = company.lower().replace(" ", "")
    domain = f"{slug}.example"
    entity = Entity(canonical_name=company, canonical_domain=domain)
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name=company,
        domain=domain,
        work_email=f"ceo@{domain}",
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
    return run.id, sub.id


def _sign_in(client: TestClient, engine, email: str) -> str:
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
# Tests: correct + re-run
# ---------------------------------------------------------------------------


class TestCorrectAndRerun:
    def test_correct_triggers_reanalysis(self, client, wf_engine):
        """Authenticated operator corrects a field → 202 with new_run_id."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Correct Co A")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "correct_op_a@test.example")

        with mock.patch("app.pipeline.orchestrator.enqueue_run") as mock_enq:
            mock_enq.return_value = None
            resp = client.post(
                f"/workflow/runs/{run_id}/correct",
                json={"corrections": {"company_name": "Correct Co A LLC"}},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["supersedes_run_id"] == run_id
        assert data["new_run_id"] != run_id
        assert data["corrections_applied"]["company_name"] == "Correct Co A LLC"

    def test_correct_passes_background_tasks_to_enqueue(self, client, wf_engine):
        """Correct-and-re-run hands enqueue a BackgroundTasks (eager defers)."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Correct Co BG")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "correct_op_bg@test.example")

        with mock.patch("app.pipeline.orchestrator.enqueue_run") as mock_enq:
            resp = client.post(
                f"/workflow/runs/{run_id}/correct",
                json={"corrections": {"company_name": "Correct Co BG LLC"}},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202, resp.text
        new_run_id, background = mock_enq.call_args.args
        assert new_run_id == resp.json()["new_run_id"]
        assert isinstance(background, BackgroundTasks)

    def test_corrections_new_run_supersedes_prior(self, client, wf_engine):
        """New run from correction has supersedes_id pointing to prior run."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Correct Co B")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "correct_op_b@test.example")

        with mock.patch("app.pipeline.orchestrator.enqueue_run") as mock_enq:
            mock_enq.return_value = None
            resp = client.post(
                f"/workflow/runs/{run_id}/correct",
                json={"corrections": {"country": "GB"}},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202
        new_run_id = resp.json()["new_run_id"]

        check_db = TestingSessionLocal()
        try:
            new_run = check_db.get(VerificationRun, new_run_id)
            assert new_run is not None
            assert new_run.supersedes_id == run_id
        finally:
            check_db.close()

    def test_corrections_recorded_in_audit(self, client, wf_engine):
        """Audit event includes before/after values for each corrected field."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Correct Co C")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "correct_op_c@test.example")

        with mock.patch("app.pipeline.orchestrator.enqueue_run") as mock_enq:
            mock_enq.return_value = None
            resp = client.post(
                f"/workflow/runs/{run_id}/correct",
                json={"corrections": {"company_name": "Fixed Name"}},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 202
        new_run_id = resp.json()["new_run_id"]

        check_db = TestingSessionLocal()
        try:
            event = (
                check_db.query(AuditEvent)
                .filter(
                    AuditEvent.event_type == "operator.correct_and_rerun",
                    AuditEvent.verification_run_id == new_run_id,
                )
                .first()
            )
            assert event is not None
            assert "corrections" in event.payload
            assert "before" in event.payload
            assert event.payload["corrections"]["company_name"] == "Fixed Name"
        finally:
            check_db.close()

    def test_invalid_field_returns_422(self, client, wf_engine):
        """Non-correctable field in corrections → 422."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Correct Co D")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "correct_op_d@test.example")
        resp = client.post(
            f"/workflow/runs/{run_id}/correct",
            json={"corrections": {"source_ip": "1.2.3.4"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_empty_corrections_returns_422(self, client, wf_engine):
        """Empty corrections dict → 422."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Correct Co E")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "correct_op_e@test.example")
        resp = client.post(
            f"/workflow/runs/{run_id}/correct",
            json={"corrections": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_unauthenticated_correct_returns_401(self, client, wf_engine):
        """No auth → 401."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Correct Co F")
        finally:
            setup_db.close()

        resp = client.post(
            f"/workflow/runs/{run_id}/correct",
            json={"corrections": {"company_name": "X"}},
        )
        assert resp.status_code == 401

    def test_unknown_run_correct_returns_404(self, client, wf_engine):
        """Unknown run_id → 404."""
        token = _sign_in(client, wf_engine, "correct_op_g@test.example")
        resp = client.post(
            "/workflow/runs/00000000-0000-0000-0000-000000000000/correct",
            json={"corrections": {"company_name": "X"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: add notes
# ---------------------------------------------------------------------------


class TestAddNotes:
    def test_add_notes_creates_review(self, client, wf_engine):
        """Adding notes when no review exists creates a review row."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Notes Co A")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "notes_op_a@test.example")
        resp = client.post(
            f"/workflow/runs/{run_id}/notes",
            json={"notes": "First note about this registration."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "First note" in data["notes"]
        assert data["run_id"] == run_id

    def test_add_notes_audit_event_recorded(self, client, wf_engine):
        """Notes addition is recorded as audit event."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Notes Co B")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "notes_op_b@test.example")
        resp = client.post(
            f"/workflow/runs/{run_id}/notes",
            json={"notes": "Audit test note."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        check_db = TestingSessionLocal()
        try:
            event = (
                check_db.query(AuditEvent)
                .filter(
                    AuditEvent.event_type == "operator.add_notes",
                    AuditEvent.verification_run_id == run_id,
                )
                .first()
            )
            assert event is not None
            assert "Audit test note" in event.payload["notes_added"]
        finally:
            check_db.close()

    def test_add_notes_appends_to_existing_review(self, client, wf_engine):
        """Adding notes to an existing review appends (not overwrites)."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Notes Co C")
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "notes_op_c@test.example")

        # First note
        client.post(
            f"/workflow/runs/{run_id}/notes",
            json={"notes": "First note."},
            headers={"Authorization": f"Bearer {token}"},
        )
        # Second note
        resp = client.post(
            f"/workflow/runs/{run_id}/notes",
            json={"notes": "Second note."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "First note" in data["notes"]
        assert "Second note" in data["notes"]

    def test_unauthenticated_notes_returns_401(self, client, wf_engine):
        """No auth → 401."""
        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            run_id, _ = _make_run(setup_db, "Notes Co D")
        finally:
            setup_db.close()

        resp = client.post(
            f"/workflow/runs/{run_id}/notes",
            json={"notes": "should fail"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: report export
# ---------------------------------------------------------------------------


class TestReportExport:
    def test_export_returns_normalized_report(self, client, wf_engine):
        """GET /reports/{run_id}/export returns the full normalized report.

        Export now requires an authenticated principal (P2-T12); an operator
        Bearer token is accepted.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        from app.models.evidence import Evidence  # noqa: PLC0415
        from app.scoring.engine import ScoringStage  # noqa: PLC0415
        from app.scoring.report import StoreReportStage  # noqa: PLC0415

        TestingSessionLocal = sessionmaker(
            bind=wf_engine, autocommit=False, autoflush=False
        )
        setup_db = TestingSessionLocal()
        try:
            entity = Entity(
                canonical_name="Export Co", canonical_domain="export.example"
            )
            setup_db.add(entity)
            setup_db.flush()
            sub = Submission(
                company_name="Export Co",
                domain="export.example",
                work_email="ceo@export.example",
                country="US",
                entity_id=entity.id,
            )
            setup_db.add(sub)
            setup_db.flush()
            run = VerificationRun(
                submission_id=sub.id,
                entity_id=entity.id,
                status="complete",
                source_availability={
                    "normalize_input": "complete",
                    "scoring": "complete",
                    "store_report": "complete",
                },
            )
            setup_db.add(run)
            setup_db.flush()
            run_id = run.id

            ev = Evidence(
                verification_run_id=run_id,
                source="opencorporates",
                tier=1,
                field="company_name",
                raw_value="Export Co",
                normalized_value="Export Co",
                confidence=0.9,
                fetched_at=datetime.now(tz=timezone.utc),
            )
            setup_db.add(ev)
            setup_db.commit()

            ScoringStage().run(run_id, setup_db, {})
            StoreReportStage().run(run_id, setup_db, {})
        finally:
            setup_db.close()

        token = _sign_in(client, wf_engine, "export-op@example.com")
        resp = client.get(
            f"/reports/{run_id}/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["run_id"] == run_id
        assert data["scores"] is not None
        assert "overall_score" in data["scores"]
        assert isinstance(data["evidence"], list)
        assert isinstance(data["mismatches"], list)
        assert isinstance(data["sources"], list)

    def test_export_unknown_run_returns_404(self, client, wf_engine):
        """GET /reports/{unknown}/export → 404 (authenticated principal)."""
        token = _sign_in(client, wf_engine, "export-op-404@example.com")
        resp = client.get(
            "/reports/00000000-0000-0000-0000-000000000000/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
