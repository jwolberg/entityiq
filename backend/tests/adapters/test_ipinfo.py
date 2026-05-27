"""Tests for P2-T2 — IPInfo network/IP intelligence adapter.

All tests are offline — a fake HTTP client is injected; no real network
calls are made.

Test scenarios (per ticket AC):
  - Residential IP in claimed country → no mismatch flag; no anonymized flag.
  - Datacenter / VPN IP → anonymized-network flag emitted.
  - IP country != company/billing country → ip_country_mismatch flag.
  - Provider unavailable → typed AdapterFailure(kind="unavailable"); run continues.
  - No source_ip → AdapterFailure(kind="not_found").
  - Repeated IP / ASN reuse flag: noted as a scoring/consistency concern;
    the adapter emits raw ASN data which the consistency + scoring layers
    (P2-T6) use for cross-submission reuse detection.
  - Pipeline stage: EnrichNetworkIPStage persists evidence and returns context.
  - Provider keyword-based datacenter detection (free tier, no privacy field).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.base import AdapterFailure, AdapterSuccess
from app.adapters.ipinfo import EnrichNetworkIPStage, IPInfoAdapter
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
    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> _FakeResponse:
        raise TimeoutError("connection timed out")


# ---------------------------------------------------------------------------
# Sample ipinfo.io response payloads
# ---------------------------------------------------------------------------

# Residential US IP (matches claimed company country US)
_RESIDENTIAL_US = {
    "ip": "8.8.8.8",
    "city": "Mountain View",
    "region": "California",
    "country": "US",
    "org": "AS15169 Google LLC",
    "timezone": "America/Los_Angeles",
}

# Datacenter IP — org name triggers keyword detection (no privacy field)
_DATACENTER_IP = {
    "ip": "52.0.0.1",
    "city": "Ashburn",
    "region": "Virginia",
    "country": "US",
    "org": "AS14618 Amazon AWS",
    "timezone": "America/New_York",
}

# VPN / proxy IP with explicit privacy field
_VPN_IP = {
    "ip": "1.2.3.4",
    "city": "Amsterdam",
    "region": "North Holland",
    "country": "NL",
    "org": "AS12345 VPN Provider",
    "privacy": {
        "vpn": True,
        "proxy": False,
        "hosting": False,
    },
}

# IP country != company country (DE IP, US company)
_MISMATCH_IP = {
    "ip": "10.0.0.1",
    "city": "Frankfurt",
    "region": "Hesse",
    "country": "DE",
    "org": "AS3320 Deutsche Telekom",
}

# Empty response (provider returned {} — no usable fields)
_EMPTY_PAYLOAD: dict = {}


# ---------------------------------------------------------------------------
# Test DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ip_engine():
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
def ip_session(ip_engine):
    connection = ip_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


def _make_run(db: Session, source_ip: str | None = "1.2.3.4") -> VerificationRun:
    entity = Entity(canonical_name="Test Co", canonical_domain="test.example")
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name="Test Co",
        domain="test.example",
        work_email="admin@test.example",
        country="US",
        source_ip=source_ip,
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
# Tests: residential IP in claimed country → no risk flags
# ---------------------------------------------------------------------------


def test_residential_ip_matching_country_no_mismatch():
    """Residential IP matching company country → no ip_country_mismatch Evidence."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(
        run_id="run-1", source_ip="8.8.8.8", company_country_iso="US"
    )
    assert isinstance(result, AdapterSuccess)
    risk_fields = {ev.field for ev in result.evidence}
    assert "ip_country_mismatch" not in risk_fields


def test_residential_ip_emits_geo_fields():
    """Standard geo fields (ip_country, ip_region, ip_city) are emitted."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(run_id="run-1", source_ip="8.8.8.8")
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "ip_country" in fields
    assert "ip_region" in fields
    assert "ip_city" in fields


def test_residential_ip_emits_asn_org():
    """ASN and ISP fields are emitted from org string."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(run_id="run-1", source_ip="8.8.8.8")
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "ip_asn" in fields
    assert "ip_isp" in fields
    assert "ip_organization" in fields


# ---------------------------------------------------------------------------
# Tests: datacenter / VPN IP → anonymized-network flag
# ---------------------------------------------------------------------------


def test_datacenter_ip_emits_anonymized_network_flag():
    """Datacenter org name (keyword match) → ip_anonymized_network evidence."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _DATACENTER_IP))
    )
    result = adapter.fetch(run_id="run-1", source_ip="52.0.0.1")
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "ip_anonymized_network" in fields


def test_vpn_ip_emits_anonymized_network_flag():
    """Explicit privacy.vpn=True → ip_anonymized_network evidence."""
    adapter = IPInfoAdapter(http_client=_FakeHttpClient(_FakeResponse(200, _VPN_IP)))
    result = adapter.fetch(run_id="run-1", source_ip="1.2.3.4")
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "ip_anonymized_network" in fields
    assert "ip_vpn" in fields


def test_vpn_flag_confidence_is_high():
    """Explicit privacy flags emit evidence with confidence >= 0.85."""
    adapter = IPInfoAdapter(http_client=_FakeHttpClient(_FakeResponse(200, _VPN_IP)))
    result = adapter.fetch(run_id="run-1", source_ip="1.2.3.4")
    assert isinstance(result, AdapterSuccess)
    anon_ev = next(
        (ev for ev in result.evidence if ev.field == "ip_anonymized_network"), None
    )
    assert anon_ev is not None
    assert anon_ev.confidence >= 0.85


# ---------------------------------------------------------------------------
# Tests: IP country != company/billing country → mismatch flag
# ---------------------------------------------------------------------------


def test_ip_country_mismatch_flag():
    """IP in DE, company in US → ip_country_mismatch evidence emitted."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _MISMATCH_IP))
    )
    result = adapter.fetch(
        run_id="run-1", source_ip="10.0.0.1", company_country_iso="US"
    )
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "ip_country_mismatch" in fields
    assert "ip_country_match" in fields


def test_ip_country_match_flag_when_same():
    """IP country matches company country → ip_country_match='true', no mismatch."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(
        run_id="run-1", source_ip="8.8.8.8", company_country_iso="US"
    )
    assert isinstance(result, AdapterSuccess)
    match_ev = next(
        (ev for ev in result.evidence if ev.field == "ip_country_match"), None
    )
    assert match_ev is not None
    assert match_ev.raw_value == "true"


def test_no_country_flags_when_no_company_country():
    """If no company_country_iso, country comparison fields are omitted."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(
        run_id="run-1", source_ip="8.8.8.8", company_country_iso=None
    )
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "ip_country_match" not in fields
    assert "ip_country_mismatch" not in fields


# ---------------------------------------------------------------------------
# Tests: repeated submissions / ASN reuse
# (The adapter emits raw ASN evidence; cross-submission reuse is a scoring concern)
# ---------------------------------------------------------------------------


def test_asn_evidence_is_emitted_for_reuse_detection():
    """ip_asn evidence is always emitted so scoring can detect ASN reuse."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(run_id="run-1", source_ip="8.8.8.8")
    assert isinstance(result, AdapterSuccess)
    asn_ev = next((ev for ev in result.evidence if ev.field == "ip_asn"), None)
    assert asn_ev is not None
    assert asn_ev.raw_value is not None


# ---------------------------------------------------------------------------
# Tests: provider unavailable → typed failure, run continues
# ---------------------------------------------------------------------------


def test_provider_timeout_returns_typed_failure():
    """Timeout → AdapterFailure(kind='timeout'), no exception raised."""
    adapter = IPInfoAdapter(http_client=_TimeoutHttpClient())
    result = adapter.fetch(run_id="run-1", source_ip="1.2.3.4")
    assert isinstance(result, AdapterFailure)
    assert result.kind == "timeout"


def test_provider_500_returns_unavailable():
    """HTTP 500 → AdapterFailure(kind='unavailable')."""
    adapter = IPInfoAdapter(http_client=_FakeHttpClient(_FakeResponse(500, {})))
    result = adapter.fetch(run_id="run-1", source_ip="1.2.3.4")
    assert isinstance(result, AdapterFailure)
    assert result.kind == "unavailable"


def test_provider_429_returns_rate_limited():
    """HTTP 429 → AdapterFailure(kind='rate_limited')."""
    adapter = IPInfoAdapter(http_client=_FakeHttpClient(_FakeResponse(429, {})))
    result = adapter.fetch(run_id="run-1", source_ip="1.2.3.4")
    assert isinstance(result, AdapterFailure)
    assert result.kind == "rate_limited"


def test_no_source_ip_returns_not_found():
    """No source_ip → AdapterFailure(kind='not_found')."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(run_id="run-1", source_ip=None)
    assert isinstance(result, AdapterFailure)
    assert result.kind == "not_found"


# ---------------------------------------------------------------------------
# Tests: evidence contract — source, tier, attribution, confidence
# ---------------------------------------------------------------------------


def test_evidence_source_is_ipinfo():
    """All evidence rows carry source='ipinfo'."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(run_id="run-1", source_ip="8.8.8.8")
    assert isinstance(result, AdapterSuccess)
    for ev in result.evidence:
        assert ev.source == "ipinfo"


def test_evidence_tier_is_2():
    """All evidence rows carry tier=2."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(run_id="run-1", source_ip="8.8.8.8")
    assert isinstance(result, AdapterSuccess)
    for ev in result.evidence:
        assert ev.tier == 2


def test_evidence_has_attribution():
    """All evidence rows carry an attribution dict with 'provider' key."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    result = adapter.fetch(run_id="run-1", source_ip="8.8.8.8")
    assert isinstance(result, AdapterSuccess)
    for ev in result.evidence:
        assert isinstance(ev.attribution, dict)
        assert ev.attribution.get("provider") == "ipinfo"


def test_evidence_confidence_in_range():
    """All evidence rows have confidence in [0.0, 1.0]."""
    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _MISMATCH_IP))
    )
    result = adapter.fetch(
        run_id="run-1", source_ip="10.0.0.1", company_country_iso="US"
    )
    assert isinstance(result, AdapterSuccess)
    for ev in result.evidence:
        assert ev.confidence is not None
        assert 0.0 <= ev.confidence <= 1.0


# ---------------------------------------------------------------------------
# Tests: pipeline stage — EnrichNetworkIPStage
# ---------------------------------------------------------------------------


def test_stage_name():
    assert EnrichNetworkIPStage.name == "enrich_network_ip"


def test_stage_persists_evidence(ip_session: Session):
    """EnrichNetworkIPStage persists Evidence rows to DB on success."""
    run = _make_run(ip_session, source_ip="8.8.8.8")

    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    stage = EnrichNetworkIPStage(adapter=adapter)
    context = {
        "normalized": {
            "source_ip": "8.8.8.8",
            "country_iso": "US",
        }
    }

    ctx_out = stage.run(run.id, ip_session, context)

    evidence = (
        ip_session.query(Evidence)
        .filter_by(verification_run_id=run.id, source="ipinfo")
        .all()
    )
    assert len(evidence) > 0
    assert ctx_out["network_ip"]["status"] == "complete"
    assert ctx_out["network_ip"]["evidence_count"] > 0


def test_stage_no_exception_on_unavailable(ip_session: Session):
    """EnrichNetworkIPStage records unavailable status without raising."""
    run = _make_run(ip_session, source_ip="1.2.3.4")

    adapter = IPInfoAdapter(http_client=_TimeoutHttpClient())
    stage = EnrichNetworkIPStage(adapter=adapter)
    context = {
        "normalized": {
            "source_ip": "1.2.3.4",
            "country_iso": "US",
        }
    }

    # Must not raise
    ctx_out = stage.run(run.id, ip_session, context)
    assert ctx_out["network_ip"]["status"] == "timeout"


def test_stage_no_source_ip_records_not_found(ip_session: Session):
    """Stage with no source_ip records not_found without raising."""
    run = _make_run(ip_session, source_ip=None)

    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    stage = EnrichNetworkIPStage(adapter=adapter)
    context = {"normalized": {"source_ip": None, "country_iso": "US"}}

    ctx_out = stage.run(run.id, ip_session, context)
    assert ctx_out["network_ip"]["status"] == "not_found"


def test_stage_preserves_prior_context(ip_session: Session):
    """Stage output includes all prior context keys."""
    run = _make_run(ip_session, source_ip="8.8.8.8")

    adapter = IPInfoAdapter(
        http_client=_FakeHttpClient(_FakeResponse(200, _RESIDENTIAL_US))
    )
    stage = EnrichNetworkIPStage(adapter=adapter)
    context = {
        "normalized": {"source_ip": "8.8.8.8", "country_iso": "US"},
        "candidates": {"status": "single_match"},
        "registries": {"status": "complete"},
    }

    ctx_out = stage.run(run.id, ip_session, context)
    assert "candidates" in ctx_out
    assert "registries" in ctx_out
    assert "network_ip" in ctx_out
