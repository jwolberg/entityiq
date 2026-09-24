"""Tests for P1-T3 — Pipeline stage: normalize input.

Tests cover both the pure-function helpers and the NormalizeInputStage.run()
method via a full orchestrator round-trip on SQLite in-memory.

No live Postgres or Redis required.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base
from app.models.entity import Entity
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.normalize import (
    NormalizeInputStage,
    format_tax_id,
    normalize_country,
    normalize_domain,
    normalize_email,
)

# ---------------------------------------------------------------------------
# Test DB fixtures (module-scoped, independent of other test modules)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def norm_engine():
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
def norm_session(norm_engine):
    connection = norm_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helper: create a minimal run for the stage to operate on
# ---------------------------------------------------------------------------


def _make_run(
    db: Session,
    *,
    domain: str = "acme.example",
    country: str = "US",
    email: str = "cto@acme.example",
    billing_address: str | None = None,
    tax_id: str | None = None,
) -> VerificationRun:
    entity = Entity(canonical_name="Test Co", canonical_domain=domain)
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name="Test Co",
        domain=domain,
        work_email=email,
        country=country,
        billing_address=billing_address,
        tax_id=tax_id,
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


# ---------------------------------------------------------------------------
# Tests: normalize_domain helper
# ---------------------------------------------------------------------------


def test_normalize_domain_lowercase():
    assert normalize_domain("ACME.EXAMPLE") == "acme.example"


def test_normalize_domain_strips_https_scheme():
    assert normalize_domain("https://acme.example") == "acme.example"


def test_normalize_domain_strips_http_scheme():
    assert normalize_domain("http://acme.example") == "acme.example"


def test_normalize_domain_strips_path():
    assert normalize_domain("https://acme.example/about/us") == "acme.example"


def test_normalize_domain_strips_port():
    assert normalize_domain("acme.example:8080") == "acme.example"


def test_normalize_domain_strips_query():
    assert normalize_domain("acme.example?ref=1") == "acme.example"


def test_normalize_domain_empty_returns_none():
    assert normalize_domain("") is None


def test_normalize_domain_mixed_case_with_scheme():
    assert normalize_domain("HTTPS://ACME.EXAMPLE/PATH") == "acme.example"


# ---------------------------------------------------------------------------
# Tests: normalize_country helper
# ---------------------------------------------------------------------------


def test_normalize_country_iso_code_uppercase_passthrough():
    assert normalize_country("US") == "US"


def test_normalize_country_full_name_us():
    assert normalize_country("United States") == "US"


def test_normalize_country_full_name_uk():
    assert normalize_country("United Kingdom") == "GB"


def test_normalize_country_alias_usa():
    assert normalize_country("usa") == "US"


def test_normalize_country_alias_uk():
    assert normalize_country("uk") == "GB"


def test_normalize_country_lowercase_code():
    # 2-letter lowercase — not an ISO2 match (regex requires uppercase),
    # but falls through to the dict lookup.
    assert normalize_country("us") == "US"


def test_normalize_country_germany():
    assert normalize_country("Germany") == "DE"


def test_normalize_country_deutschland():
    assert normalize_country("Deutschland") == "DE"


def test_normalize_country_unsupported_returns_none():
    assert normalize_country("Narnia") is None


def test_normalize_country_empty_returns_none():
    assert normalize_country("") is None


def test_normalize_country_whitespace_stripped():
    assert normalize_country("  US  ") == "US"


# ---------------------------------------------------------------------------
# Tests: normalize_email helper
# ---------------------------------------------------------------------------


def test_normalize_email_lowercase():
    assert normalize_email("CTO@ACME.EXAMPLE") == "cto@acme.example"


def test_normalize_email_strips_whitespace():
    assert normalize_email("  user@example.com  ") == "user@example.com"


def test_normalize_email_empty_returns_none():
    assert normalize_email("") is None


# ---------------------------------------------------------------------------
# Tests: NormalizeInputStage.run() via direct stage call
# ---------------------------------------------------------------------------


def test_normalize_stage_returns_normalized_context(norm_session: Session):
    run = _make_run(
        norm_session,
        domain="HTTPS://ACME.EXAMPLE/PATH",
        country="United States",
        email="CTO@ACME.EXAMPLE",
        billing_address="  123 Main St  ",
        tax_id="  12-345  ",
    )
    stage = NormalizeInputStage()
    ctx = stage.run(run.id, norm_session, {})

    assert "normalized" in ctx
    n = ctx["normalized"]
    assert n["domain"] == "acme.example"
    assert n["country_iso"] == "US"
    assert n["email"] == "cto@acme.example"
    assert n["billing_address"] == "123 Main St"
    assert n["tax_id"] == "12-345"


def test_normalize_stage_preserves_existing_context_keys(norm_session: Session):
    """Stage must not remove keys set by earlier stages."""
    run = _make_run(norm_session, domain="beta.example", country="GB")
    stage = NormalizeInputStage()
    prior_ctx = {"prior_stage_key": "prior_value"}
    ctx = stage.run(run.id, norm_session, prior_ctx)
    assert ctx["prior_stage_key"] == "prior_value"
    assert "normalized" in ctx


def test_normalize_stage_mixed_case_domain(norm_session: Session):
    run = _make_run(norm_session, domain="BETA.EXAMPLE", country="CA")
    stage = NormalizeInputStage()
    ctx = stage.run(run.id, norm_session, {})
    assert ctx["normalized"]["domain"] == "beta.example"


def test_normalize_stage_country_mapped_to_iso(norm_session: Session):
    run = _make_run(norm_session, domain="gamma.example", country="Germany")
    stage = NormalizeInputStage()
    ctx = stage.run(run.id, norm_session, {})
    assert ctx["normalized"]["country_iso"] == "DE"


def test_normalize_stage_unsupported_country_is_none_not_raised(
    norm_session: Session,
):
    """An unrecognised country must not raise — records None."""
    run = _make_run(norm_session, domain="delta.example", country="Wakanda")
    stage = NormalizeInputStage()
    # Must not raise
    ctx = stage.run(run.id, norm_session, {})
    assert ctx["normalized"]["country_iso"] is None


def test_normalize_stage_malformed_domain_handled(norm_session: Session):
    """A domain that reduces to empty string after stripping must not raise."""
    run = _make_run(norm_session, domain="https://", country="US")
    stage = NormalizeInputStage()
    ctx = stage.run(run.id, norm_session, {})
    # domain strips to '' → normalize_domain returns None
    assert ctx["normalized"]["domain"] is None


# ---------------------------------------------------------------------------
# Tests: format_tax_id stub
# ---------------------------------------------------------------------------


def test_format_tax_id_strips_whitespace():
    assert format_tax_id("  12-345  ", "US") == "12-345"


def test_format_tax_id_none_input_returns_none():
    assert format_tax_id(None, "US") is None


def test_format_tax_id_empty_input_returns_none():
    assert format_tax_id("", "US") is None


def test_format_tax_id_unknown_country_returns_stripped():
    """Stub: returns stripped value for any country."""
    assert format_tax_id("  ABC-123  ", "ZZ") == "ABC-123"


# ---------------------------------------------------------------------------
# P4-T2 — free/disposable email becomes scored evidence
# ---------------------------------------------------------------------------


def _intake_evidence(db, run_id):
    from app.models.evidence import Evidence

    return {
        e.field: e
        for e in db.query(Evidence).filter(Evidence.verification_run_id == run_id)
    }


def test_free_email_domain_is_recorded_as_intake_evidence(norm_session):
    run = _make_run(norm_session, email="founder@gmail.com")
    NormalizeInputStage().run(run.id, norm_session, {})
    ev = _intake_evidence(norm_session, run.id)["free_email_domain"]
    assert ev.normalized_value == "true"
    assert ev.source == "submission"
    assert ev.tier == 0  # intake metadata: must not count as source coverage


def test_company_email_records_no_free_email_evidence(norm_session):
    run = _make_run(norm_session, email="cto@acme.example")
    NormalizeInputStage().run(run.id, norm_session, {})
    assert "free_email_domain" not in _intake_evidence(norm_session, run.id)
