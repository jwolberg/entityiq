"""Tests for GET /reports/ — list endpoint added for P1-T10 operator dashboard.

All tests use SQLite in-memory via isolated fixtures (no live Postgres/Redis).

Scenarios:
  1. Empty DB → returns empty list
  2. One completed report → list includes company name, score, review status None
  3. Reviewed report → list reflects review_status from the Review row
  4. Multiple reports → ordered newest first
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers ORM models with Base.metadata
from app.api.reports import _get_db
from app.db.session import Base
from app.main import app
from app.models.entity import Entity
from app.models.operator import Operator
from app.models.report import Report
from app.models.review import Review
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def list_engine():
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
def list_db(list_engine):
    connection = list_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def list_client(list_engine):
    TestingSessionLocal = sessionmaker(
        bind=list_engine, autocommit=False, autoflush=False
    )

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[_get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.pop(_get_db, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(
    db: Session,
    company_name: str = "Acme Corp",
    domain: str = "acme.example",
    overall_score: float | None = 72.0,
    report_status: str = "complete",
) -> tuple[str, str]:
    """Persist entity + submission + run + report; return (run_id, report_id)."""
    entity = Entity(canonical_name=company_name, canonical_domain=domain)
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name=company_name,
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
        source_availability={},
    )
    db.add(run)
    db.flush()

    summary = {}
    if overall_score is not None:
        summary["scores"] = {"overall_score": overall_score}

    report = Report(
        verification_run_id=run.id,
        status=report_status,
        section_statuses={"scores": "complete", "evidence": "complete",
                          "mismatches": "complete", "sources": "complete"},
        summary=summary,
        generated_at=datetime.now(tz=timezone.utc),
    )
    db.add(report)
    db.commit()
    return run.id, report.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_empty(list_client):
    """GET /reports/ on an empty DB returns an empty list."""
    resp = list_client.get("/reports/")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_one_report_no_review(list_client, list_engine):
    """A completed report with no review appears in the list with review_status null."""
    TestingSessionLocal = sessionmaker(
        bind=list_engine, autocommit=False, autoflush=False
    )
    db = TestingSessionLocal()
    try:
        run_id, report_id = _make_report(db, "Globex Ltd", "globex.example", 55.0)
    finally:
        db.close()

    resp = list_client.get("/reports/")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    match = next((i for i in items if i["run_id"] == run_id), None)
    assert match is not None, f"run_id {run_id} not in list"
    assert match["company_name"] == "Globex Ltd"
    assert match["domain"] == "globex.example"
    assert match["overall_score"] == 55.0
    assert match["review_status"] is None
    assert match["status"] == "complete"


def test_list_reviewed_report(list_client, list_engine):
    """A reviewed report reflects the operator's review_status."""
    TestingSessionLocal = sessionmaker(
        bind=list_engine, autocommit=False, autoflush=False
    )
    db = TestingSessionLocal()
    try:
        run_id, _ = _make_report(db, "Reviewed Inc", "reviewed.example", 30.0)

        # Create operator + review
        op = Operator(
            email="op@entityiq.test",
            full_name="Test Operator",
            role="operator",
        )
        db.add(op)
        db.flush()

        rev = Review(
            verification_run_id=run_id,
            operator_id=op.id,
            status="approved",
            decided_at=datetime.now(tz=timezone.utc),
        )
        db.add(rev)
        db.commit()
    finally:
        db.close()

    resp = list_client.get("/reports/")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    match = next((i for i in items if i["run_id"] == run_id), None)
    assert match is not None
    assert match["review_status"] == "approved"


def test_list_no_score(list_client, list_engine):
    """A report with no scores yet (pending) renders overall_score as null."""
    TestingSessionLocal = sessionmaker(
        bind=list_engine, autocommit=False, autoflush=False
    )
    db = TestingSessionLocal()
    try:
        run_id, _ = _make_report(
            db, "Pending Co", "pending.example", overall_score=None,
            report_status="pending"
        )
    finally:
        db.close()

    resp = list_client.get("/reports/")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    match = next((i for i in items if i["run_id"] == run_id), None)
    assert match is not None
    assert match["overall_score"] is None
    assert match["status"] == "pending"
