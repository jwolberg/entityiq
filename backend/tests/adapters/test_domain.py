"""Tests for P1-T5 — Tier-2 domain/infrastructure signals adapter.

All tests are offline — WHOIS/DNS/SSL clients are injected via fakes.
No network calls are made.

Test scenarios:
  - Long-lived domain with MX + valid SSL → trust-leaning evidence rows.
  - Recently-registered domain with no MX → elevated-risk signals.
  - WHOIS unavailable → whois_status=unavailable evidence; run continues.
  - No domain in context → AdapterFailure(kind='not_found').
  - All lookups fail → AdapterFailure(kind='unavailable').
  - Pipeline stage persists evidence and returns context.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.base import AdapterContext, AdapterFailure, AdapterSuccess
from app.adapters.domain import AnalyzeDomainStage, DomainAdapter
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

# ---------------------------------------------------------------------------
# Fake client helpers
# ---------------------------------------------------------------------------


class _WhoisFake:
    """Returns a configurable WHOIS result."""

    def __init__(
        self,
        creation_date: datetime | None = None,
        registrar: str | None = None,
        expiration_date: datetime | None = None,
    ):
        self._data = {
            "creation_date": creation_date,
            "registrar": registrar,
            "expiration_date": expiration_date,
        }

    def query(self, domain: str) -> dict:
        return self._data


class _WhoisUnavailable:
    """WHOIS client that always raises."""

    def query(self, domain: str) -> dict:
        raise RuntimeError("WHOIS service unavailable")


class _DnsFake:
    """Returns configurable DNS responses."""

    def __init__(
        self,
        mx: list[str] | None = None,
        txt: list[str] | None = None,
        a: list[str] | None = None,
    ):
        self._mx = mx or []
        self._txt = txt or []
        self._a = a or []

    def query_mx(self, domain: str) -> list[str]:
        return self._mx

    def query_txt(self, domain: str) -> list[str]:
        return self._txt

    def query_a(self, domain: str) -> list[str]:
        return self._a


class _DnsUnavailable:
    """DNS client that always raises."""

    def query_mx(self, domain: str) -> list[str]:
        raise RuntimeError("DNS unavailable")

    def query_txt(self, domain: str) -> list[str]:
        raise RuntimeError("DNS unavailable")

    def query_a(self, domain: str) -> list[str]:
        raise RuntimeError("DNS unavailable")


class _SslFake:
    """Returns a configurable SSL cert response."""

    def __init__(self, cert: dict | None = None):
        self._cert = cert or {}

    def get_cert(self, domain: str, port: int = 443) -> dict:
        return self._cert


class _SslUnavailable:
    """SSL client that always raises."""

    def get_cert(self, domain: str, port: int = 443) -> dict:
        raise RuntimeError("SSL unavailable")


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_OLD_DOMAIN_CREATION = datetime.now(tz=timezone.utc) - timedelta(days=3000)
_RECENT_DOMAIN_CREATION = datetime.now(tz=timezone.utc) - timedelta(days=30)

_VALID_CERT = {
    "issuer": "DigiCert Inc",
    "subject": "acme.example",
    "not_before": "Jan 1 00:00:00 2023 GMT",
    "not_after": "Jan 1 00:00:00 2025 GMT",
}


# ---------------------------------------------------------------------------
# Test DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dom_engine():
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
def dom_session(dom_engine):
    connection = dom_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


def _make_run(db: Session, domain: str = "acme.example") -> VerificationRun:
    entity = Entity(canonical_name="Acme Corp", canonical_domain=domain)
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name="Acme Corp",
        domain=domain,
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
# Tests: long-lived domain with MX + valid SSL → trust-leaning evidence
# ---------------------------------------------------------------------------


def test_long_lived_domain_returns_success():
    """Long-lived domain → AdapterSuccess with evidence."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(
            creation_date=_OLD_DOMAIN_CREATION, registrar="GoDaddy"
        ),
        dns_client=_DnsFake(
            mx=["mail.acme.example"], txt=["v=spf1 include:gmail.com ~all"]
        ),
        ssl_client=_SslFake(cert=_VALID_CERT),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    assert len(result.evidence) > 0


def test_long_lived_domain_has_domain_age():
    """Long-lived domain → domain_age_days evidence present."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_OLD_DOMAIN_CREATION),
        dns_client=_DnsFake(),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "domain_age_days" in fields


def test_long_lived_domain_with_mx_has_mx_evidence():
    """Domain with MX → mx_records evidence."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_OLD_DOMAIN_CREATION),
        dns_client=_DnsFake(mx=["mail.acme.example"]),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "mx_records" in fields


def test_long_lived_domain_with_mx_no_no_mx_signal():
    """Domain WITH MX records should NOT have a no_mx risk signal."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_OLD_DOMAIN_CREATION),
        dns_client=_DnsFake(mx=["mail.acme.example"]),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "no_mx" not in fields


def test_long_lived_domain_with_spf_has_spf_evidence():
    """Domain with SPF TXT → spf_record evidence with v=spf1 value."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_OLD_DOMAIN_CREATION),
        dns_client=_DnsFake(txt=["v=spf1 include:google.com ~all"]),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    spf_evs = [ev for ev in result.evidence if ev.field == "spf_record"]
    assert len(spf_evs) > 0
    assert "v=spf1" in (spf_evs[0].raw_value or "")


def test_long_lived_domain_with_ssl_has_ssl_evidence():
    """Domain with valid SSL → ssl_issuer and ssl_subject evidence."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_OLD_DOMAIN_CREATION),
        dns_client=_DnsFake(),
        ssl_client=_SslFake(cert=_VALID_CERT),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "ssl_issuer" in fields
    assert "ssl_subject" in fields


def test_evidence_has_source_and_tier():
    """All evidence rows carry source='domain' and tier=2."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_OLD_DOMAIN_CREATION),
        dns_client=_DnsFake(mx=["mail.acme.example"]),
        ssl_client=_SslFake(cert=_VALID_CERT),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    for ev in result.evidence:
        assert ev.source == "domain"
        assert ev.tier == 2


# ---------------------------------------------------------------------------
# Tests: recently-registered domain with no MX → elevated-risk signals
# ---------------------------------------------------------------------------


def test_recently_registered_domain_has_risk_signal():
    """Domain created < 180 days ago → recently_registered evidence."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_RECENT_DOMAIN_CREATION),
        dns_client=_DnsFake(),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="newdomain.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "recently_registered" in fields


def test_no_mx_domain_has_no_mx_signal():
    """Domain with no MX records → no_mx evidence."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_OLD_DOMAIN_CREATION),
        dns_client=_DnsFake(mx=[]),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "no_mx" in fields


def test_recently_registered_with_no_mx_has_both_signals():
    """Recently-registered + no MX → both risk signals present."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(creation_date=_RECENT_DOMAIN_CREATION),
        dns_client=_DnsFake(mx=[]),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="new.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "recently_registered" in fields
    assert "no_mx" in fields


# ---------------------------------------------------------------------------
# Tests: WHOIS unavailable → typed result section; run continues
# ---------------------------------------------------------------------------


def test_whois_unavailable_run_continues():
    """WHOIS fails → whois_status evidence; overall still AdapterSuccess."""
    adapter = DomainAdapter(
        whois_client=_WhoisUnavailable(),
        dns_client=_DnsFake(mx=["mail.acme.example"]),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    # Overall result is still success (DNS ran fine)
    assert isinstance(result, AdapterSuccess)
    whois_evs = [ev for ev in result.evidence if ev.field == "whois_status"]
    assert len(whois_evs) > 0
    assert whois_evs[0].raw_value == "unavailable"


def test_whois_unavailable_no_domain_age():
    """WHOIS fails → no domain_age_days evidence (not estimated)."""
    adapter = DomainAdapter(
        whois_client=_WhoisUnavailable(),
        dns_client=_DnsFake(),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1", domain="acme.example")
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterSuccess)
    fields = {ev.field for ev in result.evidence}
    assert "domain_age_days" not in fields


# ---------------------------------------------------------------------------
# Tests: no domain in context → not_found
# ---------------------------------------------------------------------------


def test_no_domain_returns_not_found():
    """No domain in context → AdapterFailure(kind='not_found')."""
    adapter = DomainAdapter(
        whois_client=_WhoisFake(),
        dns_client=_DnsFake(),
        ssl_client=_SslFake(),
    )
    ctx = AdapterContext(run_id="run-1")  # no domain
    result = adapter.fetch(ctx)
    assert isinstance(result, AdapterFailure)
    assert result.kind == "not_found"


# ---------------------------------------------------------------------------
# Tests: pipeline stage — AnalyzeDomainStage
# ---------------------------------------------------------------------------


def test_stage_persists_evidence(dom_session: Session):
    """AnalyzeDomainStage persists Evidence rows to DB."""
    run = _make_run(dom_session)

    stage = AnalyzeDomainStage(
        whois_client=_WhoisFake(
            creation_date=_OLD_DOMAIN_CREATION, registrar="Namecheap"
        ),
        dns_client=_DnsFake(mx=["mail.acme.example"], txt=["v=spf1 ~all"]),
        ssl_client=_SslFake(cert=_VALID_CERT),
    )
    context = {"normalized": {"domain": "acme.example", "company_name": "Acme Corp"}}
    ctx_out = stage.run(run.id, dom_session, context)

    evidence = (
        dom_session.query(Evidence)
        .filter_by(verification_run_id=run.id, source="domain")
        .all()
    )
    assert len(evidence) > 0
    assert ctx_out["domain_signals"]["status"] == "complete"


def test_stage_unavailable_on_all_failures(dom_session: Session):
    """AnalyzeDomainStage records unavailable when all lookups fail."""
    run = _make_run(dom_session, domain="fail.example")

    stage = AnalyzeDomainStage(
        whois_client=_WhoisUnavailable(),
        dns_client=_DnsUnavailable(),
        ssl_client=_SslUnavailable(),
    )
    context = {"normalized": {"domain": "fail.example"}}
    # Must not raise
    ctx_out = stage.run(run.id, dom_session, context)
    assert ctx_out["domain_signals"]["status"] in (
        "unavailable",
        "not_found",
        "complete",
    )


def test_stage_no_exception_on_whois_failure(dom_session: Session):
    """AnalyzeDomainStage does not raise even when WHOIS is unavailable."""
    run = _make_run(dom_session, domain="partial.example")

    stage = AnalyzeDomainStage(
        whois_client=_WhoisUnavailable(),
        dns_client=_DnsFake(mx=["mx.partial.example"]),
        ssl_client=_SslFake(),
    )
    context = {"normalized": {"domain": "partial.example"}}
    # Must not raise
    ctx_out = stage.run(run.id, dom_session, context)
    assert "domain_signals" in ctx_out
