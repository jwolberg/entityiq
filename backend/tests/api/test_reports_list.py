"""Tests for GET /reports/ — list endpoint added for P1-T10 operator dashboard.

All tests use SQLite in-memory via isolated fixtures (no live Postgres/Redis).

Scenarios:
  1. Empty DB → returns empty list
  2. One completed report → list includes company name, score, review status None
  3. Reviewed report → list reflects review_status from the Review row
  4. Multiple reports → ordered newest first
  5. Ticket 0089: one bounded query, pagination, server-side tier / review /
     search filters (tier and score come from the linked risk_assessment)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers ORM models with Base.metadata
from app.api.reports import _get_db
from app.auth.service import Principal, get_principal
from app.db.session import Base
from app.main import app
from app.models.entity import Entity
from app.models.operator import Operator
from app.models.report import Report
from app.models.review import Review
from app.models.risk_assessment import RiskAssessment
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
# Helpers
# ---------------------------------------------------------------------------


def _make_report(
    db: Session,
    company_name: str = "Acme Corp",
    domain: str = "acme.example",
    overall_score: float | None = 72.0,
    report_status: str = "complete",
    triage_tier: str | None = None,
    created_at: datetime | None = None,
) -> tuple[str, str]:
    """Persist entity + submission + run + report; return (run_id, report_id).

    Mirrors assemble_report: a scored report links its RiskAssessment and
    copies the same score / tier into ``summary["scores"]``.
    """
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
    assessment = None
    if overall_score is not None or triage_tier is not None:
        summary["scores"] = {"overall_score": overall_score, "triage_tier": triage_tier}
        assessment = RiskAssessment(
            verification_run_id=run.id,
            overall_score=overall_score,
            triage_tier=triage_tier,
        )
        db.add(assessment)
        db.flush()

    report = Report(
        verification_run_id=run.id,
        risk_assessment_id=assessment.id if assessment else None,
        status=report_status,
        section_statuses={
            "scores": "complete",
            "evidence": "complete",
            "mismatches": "complete",
            "sources": "complete",
        },
        summary=summary,
        generated_at=datetime.now(tz=timezone.utc),
    )
    if created_at is not None:
        report.created_at = created_at
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
            db,
            "Pending Co",
            "pending.example",
            overall_score=None,
            report_status="pending",
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


def test_list_exposes_triage_tier(list_client, list_engine):
    """The queue must show the tier: a sanctions hit escalates even at a low
    score, so the score alone would render it as low risk."""
    SessionMaker = sessionmaker(bind=list_engine, autocommit=False, autoflush=False)
    db = SessionMaker()
    try:
        run_id, _ = _make_report(
            db, "Volga Ltd", "volga.example", 22.0, triage_tier="escalate"
        )
    finally:
        db.close()

    items = list_client.get("/reports").json()["items"]
    match = next(i for i in items if i["run_id"] == run_id)
    assert match["triage_tier"] == "escalate"


def test_list_filters_by_triage_tier(list_client, list_engine):
    """0023: filtering by triage_tier, not score band, so a sanctions hit at
    a low score is still found by `?triage_tier=escalate`."""
    SessionMaker = sessionmaker(bind=list_engine, autocommit=False, autoflush=False)
    db = SessionMaker()
    try:
        escalate_run_id, _ = _make_report(
            db,
            "Volga Escalate Ltd",
            "volga-escalate.example",
            22.0,
            triage_tier="escalate",
        )
        review_run_id, _ = _make_report(
            db, "Steady Review Inc", "steady-review.example", 55.0, triage_tier="review"
        )
        clear_run_id, _ = _make_report(
            db, "Clearview Co", "clearview.example", 12.0, triage_tier="pre_clear"
        )
    finally:
        db.close()

    resp = list_client.get("/reports", params={"triage_tier": "escalate"})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    run_ids = {i["run_id"] for i in items}

    # A low-score sanctions escalation is included — the point of this ticket.
    assert escalate_run_id in run_ids
    match = next(i for i in items if i["run_id"] == escalate_run_id)
    assert match["overall_score"] == 22.0
    assert match["triage_tier"] == "escalate"

    # Other tiers are excluded.
    assert review_run_id not in run_ids
    assert clear_run_id not in run_ids

    # No filter at all → both tiers still present (existing behavior).
    unfiltered_ids = {i["run_id"] for i in list_client.get("/reports").json()["items"]}
    assert {escalate_run_id, review_run_id, clear_run_id} <= unfiltered_ids


# ---------------------------------------------------------------------------
# Ticket 0089: bounded queries, pagination, server-side filters
# ---------------------------------------------------------------------------


def _seed(list_engine, rows: list[dict]) -> list[str]:
    """Create reports (oldest first) and return their run ids in that order."""
    SessionMaker = sessionmaker(bind=list_engine, autocommit=False, autoflush=False)
    db = SessionMaker()
    run_ids = []
    try:
        for row in rows:
            run_id, report_id = _make_report(db, **row)
            run_ids.append(run_id)
    finally:
        db.close()
    return run_ids


def _review(list_engine, run_id: str, status: str = "approved") -> None:
    SessionMaker = sessionmaker(bind=list_engine, autocommit=False, autoflush=False)
    db = SessionMaker()
    try:
        op = Operator(email=f"{run_id}@entityiq.test", full_name="Op", role="operator")
        db.add(op)
        db.flush()
        db.add(
            Review(
                verification_run_id=run_id,
                operator_id=op.id,
                status=status,
                decided_at=datetime.now(tz=timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()


def _count_statements(list_engine, fn) -> int:
    statements: list[str] = []

    def _listener(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(list_engine, "before_cursor_execute", _listener)
    try:
        fn()
    finally:
        event.remove(list_engine, "before_cursor_execute", _listener)
    return len(statements)


def test_list_query_count_does_not_grow_with_rows(list_client, list_engine):
    """No N+1: the list costs the same statements for 1 report as for 25."""
    _seed(
        list_engine,
        [dict(company_name="Qc One", domain="qc-1.querycount.example")],
    )
    one = _count_statements(
        list_engine, lambda: list_client.get("/reports", params={"q": "querycount"})
    )
    rows = [
        dict(company_name=f"Qc {i}", domain=f"qc-{i}.querycount.example")
        for i in range(2, 26)
    ]
    run_ids = _seed(list_engine, rows)
    _review(list_engine, run_ids[0])

    def _get_many():
        resp = list_client.get("/reports", params={"q": "querycount"})
        assert resp.json()["total"] == 25

    many = _count_statements(list_engine, _get_many)
    assert many == one
    assert many <= 2  # one page query + one COUNT


def test_list_paginates_newest_first(list_client, list_engine):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    run_ids = _seed(
        list_engine,
        [
            dict(
                company_name=f"Pager {i}",
                domain=f"p{i}.pagetest.example",
                created_at=base + timedelta(minutes=i),
            )
            for i in range(5)
        ],
    )
    newest_first = list(reversed(run_ids))

    seen = []
    for offset in (0, 2, 4):
        resp = list_client.get(
            "/reports", params={"q": "pagetest", "limit": 2, "offset": offset}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == offset
        seen += [i["run_id"] for i in body["items"]]

    assert seen == newest_first


def test_list_default_and_max_page_size(list_client):
    body = list_client.get("/reports").json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert list_client.get("/reports", params={"limit": 501}).status_code == 422
    assert list_client.get("/reports", params={"limit": 0}).status_code == 422


def test_list_search_matches_name_or_domain_case_insensitively(
    list_client, list_engine
):
    by_name, by_domain, other = _seed(
        list_engine,
        [
            dict(company_name="Zanzibar Searchable Co", domain="zsc.example"),
            dict(company_name="Other Name", domain="zanzibar-searchable.example"),
            dict(company_name="Unrelated", domain="unrelated-search.example"),
        ],
    )
    items = list_client.get("/reports", params={"q": "ZANZIBAR"}).json()["items"]
    assert {i["run_id"] for i in items} == {by_name, by_domain}


def test_list_search_treats_wildcards_literally(list_client, list_engine):
    (pct,) = _seed(
        list_engine, [dict(company_name="100% Literal Ltd", domain="pct.example")]
    )
    items = list_client.get("/reports", params={"q": "100%"}).json()["items"]
    assert [i["run_id"] for i in items] == [pct]
    assert list_client.get("/reports", params={"q": "_%_%_%_%"}).json()["total"] == 0


def test_list_filters_by_review_status(list_client, list_engine):
    pending, reviewed = _seed(
        list_engine,
        [
            dict(company_name="Rv Pending", domain="pending.reviewfilter.example"),
            dict(company_name="Rv Done", domain="done.reviewfilter.example"),
        ],
    )
    _review(list_engine, reviewed)

    def ids(status):
        params = {"q": "reviewfilter", "review_status": status}
        return [
            i["run_id"]
            for i in list_client.get("/reports", params=params).json()["items"]
        ]

    assert ids("pending") == [pending]
    assert ids("reviewed") == [reviewed]
    assert (
        list_client.get("/reports", params={"review_status": "bogus"}).status_code
        == 422
    )


def test_list_tier_filter_pages_and_counts_in_sql(list_client, list_engine):
    """The tier filter applies before paging, so total and pages agree."""
    _seed(
        list_engine,
        [
            dict(
                company_name=f"Tier {i}",
                domain=f"t{i}.tierpage.example",
                triage_tier="escalate" if i % 2 else "review",
            )
            for i in range(6)
        ],
    )
    body = list_client.get(
        "/reports",
        params={"q": "tierpage", "triage_tier": "escalate", "limit": 2},
    ).json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert {i["triage_tier"] for i in body["items"]} == {"escalate"}
