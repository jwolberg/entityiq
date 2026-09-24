"""Tests for P1-T8 — Report retrieval endpoint.

All tests use SQLite in-memory via the shared conftest fixtures.
No live Postgres or Redis required.

Scenarios:
  1. Completed run → full report with scores/evidence/mismatches/sources
  2. In-progress run → partial report with pending sections
  3. Unknown run_id → 404
  4. Known run, no report assembled yet → 404
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.field_comparison import FieldComparison
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.scoring.engine import ScoringStage
from app.scoring.report import StoreReportStage

# ---------------------------------------------------------------------------
# Fixtures — module-scoped DB, function-scoped session + client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reports_engine():
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
def db_sess(reports_engine):
    connection = reports_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def report_client(reports_engine):
    """TestClient wired to the SQLite in-memory DB."""
    TestingSessionLocal = sessionmaker(
        bind=reports_engine,
        autocommit=False,
        autoflush=False,
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Override the DB dependency for the reports router
    app.dependency_overrides[_get_db] = override_get_db
    # Report reads require a principal (P4-T6); auth itself is covered in
    # tests/api/test_report_auth.py, so stand in an authenticated operator here.
    app.dependency_overrides[get_principal] = lambda: Principal(
        kind="operator", id="op-test", name="test@example.com", operator_id="op-test"
    )
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.pop(_get_db, None)
    app.dependency_overrides.pop(get_principal, None)


# ---------------------------------------------------------------------------
# Helper: build a minimal run with evidence + comparisons
# ---------------------------------------------------------------------------


def _make_run_with_data(db: Session) -> str:
    """Persist Entity + Submission + VerificationRun + Evidence + FieldComparison.

    Returns run_id.
    """
    entity = Entity(canonical_name="Acme Corp", canonical_domain="acme.example")
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name="Acme Corp",
        domain="acme.example",
        work_email="ceo@acme.example",
        country="US",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()

    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="running",
        source_availability={
            "normalize_input": "complete",
            "query_registries": "complete",
            "analyze_domain": "complete",
            "consistency_checks": "complete",
            "scoring": "complete",
            "store_report": "complete",
        },
    )
    db.add(run)
    db.flush()
    run_id = run.id

    # Evidence rows
    ev_name = Evidence(
        verification_run_id=run_id,
        source="opencorporates",
        tier=1,
        field="company_name",
        raw_value="Acme Corp",
        normalized_value="Acme Corp",
        confidence=0.9,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    ev_age = Evidence(
        verification_run_id=run_id,
        source="whois",
        tier=2,
        field="domain_age_days",
        raw_value="1500",
        normalized_value="1500",
        confidence=0.95,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    db.add_all([ev_name, ev_age])
    db.flush()

    # Field comparison
    fc = FieldComparison(
        verification_run_id=run_id,
        field_name="company_name",
        submitted_value="Acme Corp",
        discovered_value="Acme Corp",
        match_status="match",
        evidence_id=ev_name.id,
    )
    db.add(fc)
    db.commit()

    return run_id


# ---------------------------------------------------------------------------
# Test: completed run → full report
# ---------------------------------------------------------------------------


def test_completed_run_returns_full_report(report_client, reports_engine):
    """GET /reports/{run_id} for a fully scored run returns complete report."""
    # Set up: build run with data + run scoring + store report
    TestingSessionLocal = sessionmaker(
        bind=reports_engine, autocommit=False, autoflush=False
    )
    setup_db = TestingSessionLocal()
    try:
        run_id = _make_run_with_data(setup_db)
        ScoringStage().run(run_id, setup_db, {})
        StoreReportStage().run(run_id, setup_db, {})
    finally:
        setup_db.close()

    resp = report_client.get(f"/reports/{run_id}")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["run_id"] == run_id
    assert data["status"] in ("complete", "partial")

    # Scores present
    assert data["scores"] is not None
    assert "overall_score" in data["scores"]
    assert data["scores"]["overall_score"] is not None
    assert "triage_tier" in data["scores"]

    # Evidence present
    assert len(data["evidence"]) >= 1
    ev0 = data["evidence"][0]
    assert "source" in ev0
    assert "tier" in ev0
    assert "field" in ev0

    # Mismatches present (may be empty list if all match)
    assert "mismatches" in data
    assert isinstance(data["mismatches"], list)

    # Sources present
    assert len(data["sources"]) >= 1
    src0 = data["sources"][0]
    assert "source" in src0
    assert "evidence_count" in src0

    # Section statuses
    ss = data["section_statuses"]
    assert ss["scores"] == "complete"
    assert ss["evidence"] == "complete"


# ---------------------------------------------------------------------------
# Test: in-progress run → partial report with pending sections
# ---------------------------------------------------------------------------


def test_in_progress_run_returns_partial_report(report_client, reports_engine):
    """An in-progress run returns partial report with pending sections."""
    TestingSessionLocal = sessionmaker(
        bind=reports_engine, autocommit=False, autoflush=False
    )
    setup_db = TestingSessionLocal()
    try:
        entity = Entity(
            canonical_name="Partial Corp", canonical_domain="partial.example"
        )
        setup_db.add(entity)
        setup_db.flush()

        sub = Submission(
            company_name="Partial Corp",
            domain="partial.example",
            work_email="ceo@partial.example",
            country="US",
            entity_id=entity.id,
        )
        setup_db.add(sub)
        setup_db.flush()

        # Only query_registries has completed; scoring not done
        run = VerificationRun(
            submission_id=sub.id,
            entity_id=entity.id,
            status="running",
            source_availability={
                "normalize_input": "complete",
                "query_registries": "complete",
                "analyze_domain": "pending",
                "consistency_checks": "pending",
                "scoring": "pending",
            },
        )
        setup_db.add(run)
        setup_db.flush()
        run_id = run.id

        # Only one evidence row, no scoring yet
        ev_name = Evidence(
            verification_run_id=run_id,
            source="opencorporates",
            tier=1,
            field="company_name",
            raw_value="Partial Corp",
            normalized_value="Partial Corp",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        setup_db.add(ev_name)
        setup_db.commit()

        # Assemble partial report (no scoring yet)
        from app.scoring.report import assemble_report  # noqa: PLC0415

        assemble_report(run_id, setup_db)
    finally:
        setup_db.close()

    resp = report_client.get(f"/reports/{run_id}")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["run_id"] == run_id
    assert data["status"] == "partial"

    ss = data["section_statuses"]
    assert ss["scores"] == "pending"
    # Evidence section should be complete (query_registries ran)
    assert ss["evidence"] == "complete"


# ---------------------------------------------------------------------------
# Test: unknown run_id → 404
# ---------------------------------------------------------------------------


def test_unknown_run_id_returns_404(report_client):
    """GET /reports/{run_id} with a non-existent run_id returns 404."""
    resp = report_client.get("/reports/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test: run exists but no report assembled → 404
# ---------------------------------------------------------------------------


def test_run_exists_no_report_returns_404(report_client, reports_engine):
    """Run exists (pending) but no Report row yet → 404 with helpful message."""
    TestingSessionLocal = sessionmaker(
        bind=reports_engine, autocommit=False, autoflush=False
    )
    setup_db = TestingSessionLocal()
    try:
        entity = Entity(
            canonical_name="No Report Corp", canonical_domain="noreport.example"
        )
        setup_db.add(entity)
        setup_db.flush()

        sub = Submission(
            company_name="No Report Corp",
            domain="noreport.example",
            work_email="ceo@noreport.example",
            country="US",
            entity_id=entity.id,
        )
        setup_db.add(sub)
        setup_db.flush()

        run = VerificationRun(
            submission_id=sub.id,
            entity_id=entity.id,
            status="pending",
            source_availability={},
        )
        setup_db.add(run)
        setup_db.commit()
        run_id = run.id
    finally:
        setup_db.close()

    resp = report_client.get(f"/reports/{run_id}")
    assert resp.status_code == 404
    assert "not been assembled" in resp.json()["detail"].lower() or (
        "not found" in resp.json()["detail"].lower()
    )


# ---------------------------------------------------------------------------
# P4-T5 — review state + notes are readable on the report
# ---------------------------------------------------------------------------


def _scored_run(reports_engine) -> str:
    SessionMaker = sessionmaker(bind=reports_engine, autocommit=False, autoflush=False)
    setup_db = SessionMaker()
    try:
        run_id = _make_run_with_data(setup_db)
        ScoringStage().run(run_id, setup_db, {})
        StoreReportStage().run(run_id, setup_db, {})
    finally:
        setup_db.close()
    return run_id


def test_unreviewed_run_has_null_review(report_client, reports_engine):
    run_id = _scored_run(reports_engine)
    data = report_client.get(f"/reports/{run_id}").json()
    assert data["review"] is None


def test_reviewed_run_exposes_status_notes_and_reviewer(report_client, reports_engine):
    from app.models.operator import Operator
    from app.models.review import Review

    run_id = _scored_run(reports_engine)
    SessionMaker = sessionmaker(bind=reports_engine, autocommit=False, autoflush=False)
    db = SessionMaker()
    try:
        op = Operator(
            email="rev@example.com", full_name="Rita Reviewer", role="operator"
        )
        db.add(op)
        db.flush()
        db.add(
            Review(
                verification_run_id=run_id,
                operator_id=op.id,
                status="reviewed",
                notes="Registry confirmed by phone.",
                decided_at=datetime.now(tz=timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()

    review = report_client.get(f"/reports/{run_id}").json()["review"]
    assert review["status"] == "reviewed"
    assert review["notes"] == "Registry confirmed by phone."
    assert review["reviewer_name"] == "Rita Reviewer"
    assert review["decided_at"] is not None


# ---------------------------------------------------------------------------
# Run timing + per-stage progress (ticket 0001, plan U21)
# ---------------------------------------------------------------------------


def test_report_exposes_run_timing_and_stage_progress(report_client, reports_engine):
    TestingSessionLocal = sessionmaker(
        bind=reports_engine, autocommit=False, autoflush=False
    )
    setup_db = TestingSessionLocal()
    try:
        run_id = _make_run_with_data(setup_db)
        run = setup_db.get(VerificationRun, run_id)
        started = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
        run.status = "complete"
        run.started_at = started
        run.finished_at = started + timedelta(seconds=90)
        run.source_availability = {
            "normalize_input": "complete",
            "query_registries": "unavailable",
        }
        setup_db.commit()
        ScoringStage().run(run_id, setup_db, {})
        StoreReportStage().run(run_id, setup_db, {})
    finally:
        setup_db.close()

    data = report_client.get(f"/reports/{run_id}").json()

    assert data["run"]["status"] == "complete"
    assert data["run"]["started_at"].startswith("2026-09-24T12:00:00")
    assert data["run"]["finished_at"].startswith("2026-09-24T12:01:30")
    assert data["run"]["duration_seconds"] == 90.0
    assert data["run"]["stages"] == {
        "normalize_input": "complete",
        "query_registries": "unavailable",
    }


def test_running_run_has_no_duration_yet(report_client, reports_engine):
    TestingSessionLocal = sessionmaker(
        bind=reports_engine, autocommit=False, autoflush=False
    )
    setup_db = TestingSessionLocal()
    try:
        run_id = _make_run_with_data(setup_db)
        run = setup_db.get(VerificationRun, run_id)
        run.status = "running"
        run.started_at = datetime.now(tz=timezone.utc)
        run.finished_at = None
        run.source_availability = {"normalize_input": "complete", "scoring": "pending"}
        setup_db.commit()
        StoreReportStage().run(run_id, setup_db, {})
    finally:
        setup_db.close()

    data = report_client.get(f"/reports/{run_id}").json()

    assert data["run"]["status"] == "running"
    assert data["run"]["finished_at"] is None
    assert data["run"]["duration_seconds"] is None
    assert data["run"]["stages"]["scoring"] == "pending"
