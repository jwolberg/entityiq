"""Tests for P1-T4 — OpenCorporates Tier-1 adapter.

All tests are offline — a fake HTTP client is injected; no network calls are made.

Test scenarios:
  - Happy path: provider returns a matching company → registry Evidence rows
    with source attribution and confidence.
  - Timeout: provider raises a timeout → AdapterFailure(kind="timeout").
  - Rate-limited: provider returns 429 → AdapterFailure(kind="rate_limited").
  - Server error: provider returns 500 → AdapterFailure(kind="unavailable").
  - No results: provider returns empty companies list →
    AdapterFailure(kind="not_found").
  - No company_name in context → AdapterFailure(kind="not_found").
  - Pipeline stage: QueryRegistriesStage persists evidence and returns context.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.base import AdapterContext, AdapterFailure, AdapterSuccess
from app.adapters.opencorporates import OpenCorporatesAdapter, QueryRegistriesStage
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

# ---------------------------------------------------------------------------
# Fake HTTP client helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self._status_code = status_code
        self._payload = payload or {}

    @property
    def status_code(self) -> int:
        return self._status_code

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    """Deterministic fake that returns a pre-configured response."""

    def __init__(self, response: _FakeResponse):
        self._response = response

    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> _FakeResponse:
        return self._response


class _TimeoutHttpClient:
    """Fake that raises a TimeoutError on every request."""

    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> _FakeResponse:
        raise TimeoutError("connection timed out")


# ---------------------------------------------------------------------------
# Shared sample OpenCorporates response payload
# ---------------------------------------------------------------------------

_SAMPLE_COMPANY = {
    "company": {
        "name": "Acme Corporation",
        "company_number": "C123456",
        "current_status": "Active",
        "jurisdiction_code": "us_ca",
        "registered_address_in_full": "1 Market St, San Francisco, CA 94105",
        "opencorporates_url": "https://opencorporates.com/companies/us_ca/C123456",
    }
}

_SAMPLE_RESPONSE = {
    "results": {
        "companies": [_SAMPLE_COMPANY],
        "total_count": 1,
    }
}

_EMPTY_RESPONSE = {
    "results": {
        "companies": [],
        "total_count": 0,
    }
}


# ---------------------------------------------------------------------------
# Test DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def oc_engine():
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
def oc_session(oc_engine):
    connection = oc_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


def _make_run(db: Session) -> VerificationRun:
    entity = Entity(canonical_name="Acme Corporation", canonical_domain="acme.example")
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name="Acme Corporation",
        domain="acme.example",
        work_email="cto@acme.example",
        country="US",
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
# Tests: happy path — returns registry evidence with attribution + confidence
# ---------------------------------------------------------------------------


def test_fetch_match_returns_success():
    """Provider returns a matching company → AdapterSuccess."""
    adapter = OpenCorporatesAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _SAMPLE_RESPONSE))
    )
    ctx = AdapterContext(
        run_id="run-1",
        company_name="Acme Corporation",
        country_iso="US",
    )
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    assert len(result.evidence) > 0


def test_fetch_match_evidence_has_source_and_tier():
    """Evidence rows carry source='opencorporates' and tier=1."""
    adapter = OpenCorporatesAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _SAMPLE_RESPONSE))
    )
    ctx = AdapterContext(run_id="run-1", company_name="Acme Corporation")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    for ev in result.evidence:
        assert ev.source == "opencorporates"
        assert ev.tier == 1


def test_fetch_match_evidence_has_confidence():
    """Every evidence row has a confidence value in [0, 1]."""
    adapter = OpenCorporatesAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _SAMPLE_RESPONSE))
    )
    ctx = AdapterContext(run_id="run-1", company_name="Acme Corporation")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    for ev in result.evidence:
        assert ev.confidence is not None
        assert 0.0 <= ev.confidence <= 1.0


def test_fetch_match_evidence_has_attribution():
    """Every evidence row has an attribution dict."""
    adapter = OpenCorporatesAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _SAMPLE_RESPONSE))
    )
    ctx = AdapterContext(run_id="run-1", company_name="Acme Corporation")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    for ev in result.evidence:
        assert isinstance(ev.attribution, dict)
        assert "provider" in ev.attribution


def test_fetch_match_returns_registry_fields():
    """Evidence includes company_name, registration_number, jurisdiction, address."""
    adapter = OpenCorporatesAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _SAMPLE_RESPONSE))
    )
    ctx = AdapterContext(run_id="run-1", company_name="Acme Corporation")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "company_name" in fields
    assert "registration_number" in fields
    assert "jurisdiction" in fields
    assert "legal_address" in fields


# ---------------------------------------------------------------------------
# Tests: typed failures — no exception escapes
# ---------------------------------------------------------------------------


def test_fetch_timeout_returns_timeout_failure():
    """Timeout → AdapterFailure(kind='timeout'), no exception raised."""
    adapter = OpenCorporatesAdapter(http_client=_TimeoutHttpClient())
    ctx = AdapterContext(run_id="run-1", company_name="Acme Corporation")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterFailure)
    assert result.kind == "timeout"


def test_fetch_rate_limited_returns_rate_limited_failure():
    """HTTP 429 → AdapterFailure(kind='rate_limited')."""
    adapter = OpenCorporatesAdapter(http_client=_FakeHttpClient(_FakeResponse(429, {})))
    ctx = AdapterContext(run_id="run-1", company_name="Acme Corporation")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterFailure)
    assert result.kind == "rate_limited"


def test_fetch_server_error_returns_unavailable_failure():
    """HTTP 500 → AdapterFailure(kind='unavailable')."""
    adapter = OpenCorporatesAdapter(http_client=_FakeHttpClient(_FakeResponse(500, {})))
    ctx = AdapterContext(run_id="run-1", company_name="Acme Corporation")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterFailure)
    assert result.kind == "unavailable"


def test_fetch_empty_results_returns_not_found():
    """Provider returns empty companies list → AdapterFailure(kind='not_found')."""
    adapter = OpenCorporatesAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _EMPTY_RESPONSE))
    )
    ctx = AdapterContext(run_id="run-1", company_name="Unknown Corp")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterFailure)
    assert result.kind == "not_found"


def test_fetch_no_company_name_returns_not_found():
    """No company_name in context → AdapterFailure(kind='not_found')."""
    adapter = OpenCorporatesAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _SAMPLE_RESPONSE))
    )
    ctx = AdapterContext(run_id="run-1")  # no company_name
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterFailure)
    assert result.kind == "not_found"


# ---------------------------------------------------------------------------
# Tests: pipeline stage — QueryRegistriesStage
# ---------------------------------------------------------------------------


def test_stage_persists_evidence_on_match(oc_session: Session):
    """QueryRegistriesStage persists Evidence rows to DB on success."""
    run = _make_run(oc_session)

    fake_adapter = OpenCorporatesAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _SAMPLE_RESPONSE))
    )
    stage = QueryRegistriesStage(adapter=fake_adapter)
    context = {"normalized": {"company_name": "Acme Corporation", "country_iso": "US"}}

    ctx_out = stage.run(run.id, oc_session, context)

    evidence = (
        oc_session.query(Evidence)
        .filter_by(verification_run_id=run.id, source="opencorporates")
        .all()
    )
    assert len(evidence) > 0
    assert ctx_out["registries"]["status"] == "complete"


def test_stage_no_exception_on_unavailable(oc_session: Session):
    """QueryRegistriesStage records unavailable status without raising."""
    run = _make_run(oc_session)

    fake_adapter = OpenCorporatesAdapter(http_client=_TimeoutHttpClient())
    stage = QueryRegistriesStage(adapter=fake_adapter)
    context = {"normalized": {"company_name": "Acme Corporation"}}

    # Must not raise
    ctx_out = stage.run(run.id, oc_session, context)
    assert ctx_out["registries"]["status"] == "timeout"


def test_api_token_read_from_environment(monkeypatch):
    """OPENCORPORATES_API_TOKEN configures the token without code changes."""
    from app.adapters.opencorporates import OpenCorporatesAdapter

    seen: dict = {}

    class _Capture:
        def get(self, url, *, params=None, timeout: float = 10.0):
            seen.update(params or {})
            return _FakeResponse(200, _EMPTY_RESPONSE)

    monkeypatch.setenv("OPENCORPORATES_API_TOKEN", "tok-123")
    OpenCorporatesAdapter(http_client=_Capture()).fetch(
        AdapterContext(run_id="r", company_name="Acme")
    )
    assert seen.get("api_token") == "tok-123"
