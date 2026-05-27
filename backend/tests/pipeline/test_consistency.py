"""Tests for P1-T6 — Consistency checks → field comparisons.

All tests run offline against SQLite in-memory.  No network calls.

Test scenarios:
  - Matching company name: submitted matches discovered evidence → "match"
  - Mismatching country (registry jurisdiction ≠ submitted) → "mismatch"
  - No evidence for a field → "unverified" (not mismatch)
  - Matching address: billing_address matches legal_address evidence → "match"
  - Mismatching address → "mismatch"
  - Matching jurisdiction code → "match"
  - ConsistencyChecksStage persists FieldComparison rows and returns context.
  - Stage is not raised when run completes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.field_comparison import FieldComparison
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.consistency import (
    ConsistencyChecksStage,
    _address_match,
    _iso_match,
    _names_match,
    _run_comparisons,
)

# ---------------------------------------------------------------------------
# Test DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cons_engine():
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
def cons_session(cons_engine):
    connection = cons_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helper: create a minimal run + optional evidence
# ---------------------------------------------------------------------------


def _make_run(
    db: Session,
    *,
    company_name: str = "Acme Corp",
    domain: str = "acme.example",
    country: str = "US",
    billing_address: str | None = None,
) -> VerificationRun:
    entity = Entity(canonical_name=company_name, canonical_domain=domain)
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name=company_name,
        domain=domain,
        work_email="cto@acme.example",
        country=country,
        billing_address=billing_address,
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()

    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="pending",
    )
    db.add(run)
    db.commit()
    return run


def _add_evidence(
    db: Session,
    run_id: str,
    field: str,
    raw_value: str,
    normalized_value: str | None = None,
    source: str = "opencorporates",
    tier: int = 1,
) -> Evidence:
    ev = Evidence(
        verification_run_id=run_id,
        source=source,
        tier=tier,
        field=field,
        raw_value=raw_value,
        normalized_value=(
            normalized_value if normalized_value is not None else raw_value
        ),
        confidence=0.85,
    )
    db.add(ev)
    db.commit()
    return ev


# ---------------------------------------------------------------------------
# Tests: pure match-function helpers
# ---------------------------------------------------------------------------


def test_names_match_exact():
    assert _names_match("Acme Corp", "Acme Corp") is True


def test_names_match_case_insensitive():
    assert _names_match("acme corp", "ACME CORP") is True


def test_names_match_substring():
    assert _names_match("Acme", "Acme Corporation Ltd") is True


def test_names_match_no_match():
    assert _names_match("Acme Corp", "Beta Inc") is False


def test_names_match_empty():
    assert _names_match(None, "Acme Corp") is False
    assert _names_match("Acme Corp", None) is False


def test_iso_match_exact():
    assert _iso_match("US", "US") is True


def test_iso_match_jurisdiction_code_prefix():
    """ISO code "US" matches jurisdiction_code "US_CA"."""
    assert _iso_match("US", "us_ca") is True


def test_iso_match_no_match():
    assert _iso_match("US", "DE") is False


def test_address_match_overlap():
    assert _address_match("123 Main St", "123 Main Street, City") is True


def test_address_match_different():
    assert _address_match("123 Main St", "456 Oak Ave") is False


# ---------------------------------------------------------------------------
# Tests: matching legal name/address → match
# ---------------------------------------------------------------------------


def test_company_name_match(cons_session: Session):
    """Submitted name matches discovered evidence → FieldComparison 'match'."""
    run = _make_run(cons_session, company_name="Acme Corp")
    _add_evidence(cons_session, run.id, "company_name", "Acme Corp")

    normalized = {"company_name": "Acme Corp"}
    _run_comparisons(run.id, cons_session, normalized)

    fc = (
        cons_session.query(FieldComparison)
        .filter_by(verification_run_id=run.id, field_name="company_name")
        .first()
    )
    assert fc is not None
    assert fc.match_status == "match"


def test_company_name_case_insensitive_match(cons_session: Session):
    """Case-insensitive name comparison → match."""
    run = _make_run(cons_session, company_name="Acme Corporation")
    _add_evidence(cons_session, run.id, "company_name", "ACME CORPORATION")

    normalized = {"company_name": "Acme Corporation"}
    _run_comparisons(run.id, cons_session, normalized)

    fc = (
        cons_session.query(FieldComparison)
        .filter_by(verification_run_id=run.id, field_name="company_name")
        .first()
    )
    assert fc is not None
    assert fc.match_status == "match"


def test_billing_address_match(cons_session: Session):
    """Submitted billing_address matches discovered legal_address → match."""
    run = _make_run(
        cons_session,
        company_name="Beta Inc",
        billing_address="100 Market St, San Francisco",
    )
    _add_evidence(
        cons_session,
        run.id,
        "legal_address",
        "100 Market Street, San Francisco, CA 94105",
    )

    normalized = {
        "company_name": "Beta Inc",
        "billing_address": "100 Market St, San Francisco",
    }
    _run_comparisons(run.id, cons_session, normalized)

    fc = (
        cons_session.query(FieldComparison)
        .filter_by(verification_run_id=run.id, field_name="billing_address")
        .first()
    )
    assert fc is not None
    assert fc.match_status == "match"


# ---------------------------------------------------------------------------
# Tests: registry country ≠ submitted country → mismatch
# ---------------------------------------------------------------------------


def test_country_mismatch(cons_session: Session):
    """Registry jurisdiction 'DE' vs submitted country_iso 'US' → mismatch."""
    run = _make_run(cons_session, country="US")
    _add_evidence(
        cons_session,
        run.id,
        "jurisdiction",
        "de",
        normalized_value="DE",
    )

    normalized = {"country_iso": "US"}
    _run_comparisons(run.id, cons_session, normalized)

    fc = (
        cons_session.query(FieldComparison)
        .filter_by(verification_run_id=run.id, field_name="country_iso")
        .first()
    )
    assert fc is not None
    assert fc.match_status == "mismatch"
    assert fc.submitted_value == "US"


def test_company_name_mismatch(cons_session: Session):
    """Discovered name differs from submitted name → mismatch."""
    run = _make_run(cons_session, company_name="Acme Corp")
    _add_evidence(cons_session, run.id, "company_name", "Totally Different Inc")

    normalized = {"company_name": "Acme Corp"}
    _run_comparisons(run.id, cons_session, normalized)

    fc = (
        cons_session.query(FieldComparison)
        .filter_by(verification_run_id=run.id, field_name="company_name")
        .first()
    )
    assert fc is not None
    assert fc.match_status == "mismatch"


# ---------------------------------------------------------------------------
# Tests: no discovered value → unverified (not mismatch)
# ---------------------------------------------------------------------------


def test_no_evidence_for_field_is_unverified(cons_session: Session):
    """No evidence for a field → FieldComparison 'unverified', not 'mismatch'."""
    run = _make_run(cons_session, country="US")
    # No evidence added — no jurisdiction evidence for this run.

    normalized = {"country_iso": "US"}
    _run_comparisons(run.id, cons_session, normalized)

    fc = (
        cons_session.query(FieldComparison)
        .filter_by(verification_run_id=run.id, field_name="country_iso")
        .first()
    )
    assert fc is not None
    assert fc.match_status == "unverified"
    assert fc.discovered_value is None


def test_no_evidence_for_company_name_is_unverified(cons_session: Session):
    """No company_name evidence → unverified."""
    run = _make_run(cons_session)
    # No evidence at all.

    normalized = {"company_name": "Acme Corp"}
    _run_comparisons(run.id, cons_session, normalized)

    fc = (
        cons_session.query(FieldComparison)
        .filter_by(verification_run_id=run.id, field_name="company_name")
        .first()
    )
    assert fc is not None
    assert fc.match_status == "unverified"


# ---------------------------------------------------------------------------
# Tests: ConsistencyChecksStage pipeline stage
# ---------------------------------------------------------------------------


def test_stage_persists_comparisons(cons_session: Session):
    """ConsistencyChecksStage persists FieldComparison rows and returns context."""
    run = _make_run(cons_session, company_name="Gamma Ltd")
    _add_evidence(cons_session, run.id, "company_name", "Gamma Ltd")

    stage = ConsistencyChecksStage()
    context = {
        "normalized": {
            "company_name": "Gamma Ltd",
            "country_iso": "GB",
        }
    }
    ctx_out = stage.run(run.id, cons_session, context)

    assert ctx_out["consistency"]["status"] == "complete"
    count = ctx_out["consistency"]["comparisons"]
    assert count > 0

    fcs = (
        cons_session.query(FieldComparison).filter_by(verification_run_id=run.id).all()
    )
    assert len(fcs) == count


def test_stage_does_not_raise(cons_session: Session):
    """ConsistencyChecksStage does not raise even with empty context."""
    run = _make_run(cons_session)
    stage = ConsistencyChecksStage()
    # Must not raise
    ctx_out = stage.run(run.id, cons_session, {})
    assert "consistency" in ctx_out


def test_stage_returns_complete_status(cons_session: Session):
    """Stage context contains status='complete' on success."""
    run = _make_run(cons_session, company_name="Delta Co")
    stage = ConsistencyChecksStage()
    ctx_out = stage.run(
        run.id, cons_session, {"normalized": {"company_name": "Delta Co"}}
    )
    assert ctx_out["consistency"]["status"] == "complete"


def test_stage_sets_evidence_id_on_match(cons_session: Session):
    """FieldComparison.evidence_id is set to the matching Evidence row."""
    run = _make_run(cons_session, company_name="Echo LLC")
    ev = _add_evidence(cons_session, run.id, "company_name", "Echo LLC")

    stage = ConsistencyChecksStage()
    stage.run(run.id, cons_session, {"normalized": {"company_name": "Echo LLC"}})

    fc = (
        cons_session.query(FieldComparison)
        .filter_by(verification_run_id=run.id, field_name="company_name")
        .first()
    )
    assert fc is not None
    assert fc.match_status == "match"
    assert fc.evidence_id == ev.id
