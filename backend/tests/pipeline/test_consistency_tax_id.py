"""Tax-ID field comparisons (IC1-T2, ticket 0008).

The Tax ID row moves from a permanent "unverified" to match / mismatch /
unverified, and a Registered Name (tax ID) row compares the name on file for
the FEIN with the submitted company name.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.evidence import Evidence
from app.models.field_comparison import FieldComparison
from app.pipeline.consistency import _run_comparisons
from tests.pipeline.test_consistency import _make_run


@pytest.fixture
def cons_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


_NORMALIZED = {
    "company_name": "Acme Corporation",
    "country_iso": "US",
    "tax_id": "12-3456789",
}


def _tax_ev(db, run_id: str, field: str, value: str) -> Evidence:
    ev = Evidence(
        verification_run_id=run_id,
        source="tax_id",
        tier=1,
        field=field,
        raw_value=value,
        normalized_value=value,
        confidence=0.9,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    db.add(ev)
    db.commit()
    return ev


def _fc(db, run_id: str, field_name: str) -> FieldComparison:
    return (
        db.query(FieldComparison)
        .filter(
            FieldComparison.verification_run_id == run_id,
            FieldComparison.field_name == field_name,
        )
        .one()
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [("verified", "match"), ("inactive", "match"), ("not_found", "mismatch")],
)
def test_tax_id_row_reflects_whether_the_identifier_resolves(
    cons_session, status, expected
):
    run = _make_run(cons_session, company_name="Acme Corporation")
    ev = _tax_ev(cons_session, run.id, "tax_id_status", status)

    _run_comparisons(run.id, cons_session, _NORMALIZED)

    fc = _fc(cons_session, run.id, "tax_id")
    assert fc.match_status == expected
    assert fc.submitted_value == "12-3456789"
    assert fc.evidence_id == ev.id


def test_tax_id_row_is_unverified_without_tax_id_evidence(cons_session):
    run = _make_run(cons_session)

    _run_comparisons(run.id, cons_session, _NORMALIZED)

    fc = _fc(cons_session, run.id, "tax_id")
    assert fc.match_status == "unverified"
    assert fc.evidence_id is None


@pytest.mark.parametrize(
    ("registered", "expected"),
    [
        ("ACME CORPORATION INC.", "match"),
        ("Acme Corp", "match"),
        ("Globex Ltd", "mismatch"),
        ("Acme Europe BV", "mismatch"),
    ],
)
def test_registered_name_row_compares_name_on_file(cons_session, registered, expected):
    run = _make_run(cons_session, company_name="Acme Corporation")
    ev = _tax_ev(cons_session, run.id, "tax_id_registered_name", registered)

    _run_comparisons(run.id, cons_session, _NORMALIZED)

    fc = _fc(cons_session, run.id, "tax_id_registered_name")
    assert fc.match_status == expected
    assert fc.submitted_value == "Acme Corporation"
    assert fc.discovered_value == registered
    assert fc.evidence_id == ev.id


def test_registered_name_row_is_unverified_without_evidence(cons_session):
    run = _make_run(cons_session)

    _run_comparisons(run.id, cons_session, _NORMALIZED)

    assert _fc(cons_session, run.id, "tax_id_registered_name").match_status == (
        "unverified"
    )


def test_existing_company_name_row_ignores_tax_id_registered_name(cons_session):
    """The registry company_name comparison must not pick up tax-ID rows."""
    run = _make_run(cons_session, company_name="Acme Corporation")
    _tax_ev(cons_session, run.id, "tax_id_registered_name", "Acme Corporation")

    _run_comparisons(run.id, cons_session, _NORMALIZED)

    assert _fc(cons_session, run.id, "company_name").match_status == "unverified"
