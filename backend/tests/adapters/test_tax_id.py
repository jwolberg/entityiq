"""Tests for IC1-T1 / ticket 0007 — tax-ID (FEIN) Tier-1 adapter + stub provider.

Offline and deterministic: providers are injected. Covers the feature PRD §5
behavior and evidence contract, typed failures, non-US / missing input, the
production default (no provider configured → unavailable, never a risk
signal), and the pipeline stage.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.base import AdapterContext, AdapterFailure, AdapterSuccess
from app.adapters.tax_id import (
    ProviderRateLimited,
    StubTaxIdProvider,
    TaxIdAdapter,
    UnconfiguredTaxIdProvider,
    VerifyTaxIdStage,
    provider_from_env,
)
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.orchestrator import default_stages

_RECORDS = {
    "123456789": {"status": "active", "registered_name": "Acme Corporation Inc."},
    "987654321": {"status": "dissolved", "registered_name": "Old Shell LLC"},
}


def _ctx(**kw) -> AdapterContext:
    base = dict(
        run_id="run-1", company_name="Acme Corporation", country_iso="US", tax_id=None
    )
    base.update(kw)
    return AdapterContext(**base)


def _fields(result) -> dict[str, Evidence]:
    assert isinstance(result, AdapterSuccess), result
    return {e.field: e for e in result.evidence}


def _adapter(records=_RECORDS) -> TaxIdAdapter:
    return TaxIdAdapter(provider=StubTaxIdProvider(records))


# ---------------------------------------------------------------------------
# Evidence contract
# ---------------------------------------------------------------------------


def test_verified_active_fein_with_matching_name():
    ev = _fields(_adapter().fetch(_ctx(tax_id="12-3456789")))

    assert ev["tax_id_status"].normalized_value == "verified"
    assert ev["tax_id_registered_name"].normalized_value == "Acme Corporation Inc."
    assert ev["tax_id_name_match"].normalized_value == "match"
    for e in ev.values():
        assert e.source == "tax_id"
        assert e.tier == 1
        assert e.attribution and e.attribution["provider"] == "stub"
        assert e.verification_run_id == "run-1"


def test_fein_registered_to_a_different_name_is_a_name_mismatch():
    ev = _fields(_adapter().fetch(_ctx(tax_id="123456789", company_name="Globex Ltd")))

    assert ev["tax_id_status"].normalized_value == "verified"
    assert ev["tax_id_name_match"].normalized_value == "mismatch"


def test_dissolved_entity_reports_inactive():
    ev = _fields(_adapter().fetch(_ctx(tax_id="987654321", company_name="Old Shell")))

    assert ev["tax_id_status"].normalized_value == "inactive"
    assert ev["tax_id_status"].raw_value == "dissolved"


def test_unknown_fein_is_a_not_found_finding_not_an_outage():
    """PRD §5: not-found is a real risk signal, so it's evidence, not a failure."""
    ev = _fields(_adapter().fetch(_ctx(tax_id="111111111")))

    assert ev["tax_id_status"].normalized_value == "not_found"
    assert "tax_id_name_match" not in ev


def test_malformed_fein_is_not_found_with_reason():
    ev = _fields(_adapter().fetch(_ctx(tax_id="12-34")))

    assert ev["tax_id_status"].normalized_value == "not_found"
    assert ev["tax_id_status"].raw_payload["reason"] == "invalid_format"


# ---------------------------------------------------------------------------
# Missing input / scope → unavailable (no penalty)
# ---------------------------------------------------------------------------


def test_no_tax_id_submitted_is_unavailable():
    result = _adapter().fetch(_ctx(tax_id=None))
    assert isinstance(result, AdapterFailure)
    assert result.kind == "unavailable"


@pytest.mark.parametrize("country", ["GB", "DE", None])
def test_non_us_submission_is_unavailable_in_v1(country):
    result = _adapter().fetch(_ctx(tax_id="123456789", country_iso=country))
    assert isinstance(result, AdapterFailure)
    assert result.kind == "unavailable"


def test_unconfigured_provider_is_unavailable_never_a_finding():
    """Production default: no provider → no evidence at all, so no risk signal."""
    result = TaxIdAdapter(provider=UnconfiguredTaxIdProvider()).fetch(
        _ctx(tax_id="123456789")
    )
    assert isinstance(result, AdapterFailure)
    assert result.kind == "unavailable"


def test_provider_from_env(monkeypatch):
    monkeypatch.delenv("ENTITYIQ_TAX_ID_PROVIDER", raising=False)
    assert isinstance(provider_from_env(), UnconfiguredTaxIdProvider)
    monkeypatch.setenv("ENTITYIQ_TAX_ID_PROVIDER", "stub")
    assert isinstance(provider_from_env(), StubTaxIdProvider)
    monkeypatch.setenv("ENTITYIQ_TAX_ID_PROVIDER", "no-such-provider")
    with pytest.raises(ValueError):
        provider_from_env()


# ---------------------------------------------------------------------------
# Typed failures
# ---------------------------------------------------------------------------


class _Raises:
    name = "raising"

    def __init__(self, exc: BaseException):
        self._exc = exc

    def lookup(self, fein: str):
        raise self._exc


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (TimeoutError("slow"), "timeout"),
        (ProviderRateLimited("429"), "rate_limited"),
        (RuntimeError("boom"), "unavailable"),
    ],
)
def test_provider_errors_map_to_typed_failures(exc, kind):
    result = TaxIdAdapter(provider=_Raises(exc)).fetch(_ctx(tax_id="123456789"))
    assert isinstance(result, AdapterFailure)
    assert result.kind == kind


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
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


def _run(db: Session) -> str:
    entity = Entity(canonical_name="Acme Corporation")
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Acme Corporation",
        domain="acme.com",
        work_email="a@acme.com",
        country="US",
        tax_id="12-3456789",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(submission_id=sub.id, entity_id=entity.id, status="pending")
    db.add(run)
    db.commit()
    return run.id


def test_stage_persists_evidence_and_preserves_context(db):
    run_id = _run(db)
    stage = VerifyTaxIdStage(_adapter())
    before = {
        "prior": "kept",
        "normalized": {
            "company_name": "Acme Corporation",
            "country_iso": "US",
            "tax_id": "12-3456789",
        },
    }

    out = stage.run(run_id, db, before)

    assert out["prior"] == "kept"
    assert out["tax_id"]["status"] == "complete"
    rows = db.query(Evidence).filter(Evidence.verification_run_id == run_id).all()
    assert {r.field for r in rows} >= {"tax_id_status", "tax_id_registered_name"}


def test_stage_reports_unavailable_so_orchestrator_marks_it(db):
    run_id = _run(db)
    out = VerifyTaxIdStage(_adapter()).run(
        run_id, db, {"normalized": {"company_name": "Acme", "country_iso": "GB"}}
    )
    assert out["tax_id"]["status"] == "unavailable"
    assert db.query(Evidence).count() == 0


def test_stage_is_registered_after_query_registries():
    names = [s.name for s in default_stages()]
    assert "verify_tax_id" in names
    assert names.index("verify_tax_id") == names.index("query_registries") + 1
