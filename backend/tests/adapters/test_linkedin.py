"""Tests for IC1-T4 / ticket 0010 — LinkedIn Tier-3 adapter + stub provider.

Offline and deterministic. Resolution by linkedin_url first, else by
name + domain; unresolved search is unavailable (no penalty), but a submitted
URL that doesn't resolve is a finding. No scraping code path exists.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.base import AdapterContext, AdapterFailure, AdapterSuccess
from app.adapters.linkedin import (
    LinkedInAdapter,
    ProviderRateLimited,
    StubLinkedInProvider,
    UnconfiguredLinkedInProvider,
    VerifyLinkedInStage,
    provider_from_env,
)
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.orchestrator import default_stages

_PAGE = {
    "url": "https://www.linkedin.com/company/acme-corp",
    "name": "Acme Corporation",
    "employee_count": 1200,
    "followers": 45000,
    "founded_year": 1998,
    "website": "https://www.acme.com",
    "page_created": "2011-03-01",
    "associated_people": ["Jane Smith", "Raj Patel"],
}


def _provider() -> StubLinkedInProvider:
    return StubLinkedInProvider(pages=[_PAGE])


def _ctx(**kw) -> AdapterContext:
    base = dict(run_id="run-1", company_name="Acme Corporation", domain="acme.com")
    base.update(kw)
    return AdapterContext(**base)


def _fields(result) -> dict[str, Evidence]:
    assert isinstance(result, AdapterSuccess), result
    return {e.field: e for e in result.evidence}


def test_resolves_by_linkedin_url_and_emits_all_fields_with_attribution():
    ev = _fields(
        LinkedInAdapter(provider=_provider()).fetch(
            _ctx(
                linkedin_url="linkedin.com/company/acme-corp/",
                requester_full_name="Jane Smith",
            )
        )
    )

    assert ev["linkedin_presence"].normalized_value == "found"
    assert ev["linkedin_company_url"].normalized_value == _PAGE["url"]
    assert ev["linkedin_company_name"].normalized_value == "Acme Corporation"
    assert ev["linkedin_employee_count"].normalized_value == "1200"
    assert ev["linkedin_followers"].normalized_value == "45000"
    assert ev["linkedin_founded_year"].normalized_value == "1998"
    assert ev["linkedin_website"].normalized_value == "acme.com"
    assert ev["linkedin_page_created"].normalized_value == "2011-03-01"
    assert ev["linkedin_requester_match"].normalized_value == "true"
    for e in ev.values():
        assert e.source == "linkedin"
        assert e.tier == 3
        assert e.attribution["provider"] == "stub"
        assert e.attribution["source_url"] == _PAGE["url"]
    assert ev["linkedin_presence"].raw_payload["resolved_by"] == "url"


def test_falls_back_to_name_and_domain_search():
    ev = _fields(LinkedInAdapter(provider=_provider()).fetch(_ctx()))
    assert ev["linkedin_presence"].raw_payload["resolved_by"] == "search"
    assert ev["linkedin_company_url"].normalized_value == _PAGE["url"]


def test_requester_match_false_and_omitted():
    no_match = _fields(
        LinkedInAdapter(provider=_provider()).fetch(
            _ctx(requester_full_name="Nobody Known")
        )
    )
    assert no_match["linkedin_requester_match"].normalized_value == "false"

    no_requester = _fields(LinkedInAdapter(provider=_provider()).fetch(_ctx()))
    assert "linkedin_requester_match" not in no_requester


def test_requester_match_is_case_and_whitespace_insensitive():
    ev = _fields(
        LinkedInAdapter(provider=_provider()).fetch(
            _ctx(requester_full_name="  jane   SMITH ")
        )
    )
    assert ev["linkedin_requester_match"].normalized_value == "true"


def test_unresolvable_search_is_unavailable_no_penalty():
    result = LinkedInAdapter(provider=_provider()).fetch(
        _ctx(company_name="Globex", domain="globex.example")
    )
    assert isinstance(result, AdapterFailure)
    assert result.kind == "unavailable"


def test_submitted_url_that_does_not_resolve_is_a_not_found_finding():
    ev = _fields(
        LinkedInAdapter(provider=_provider()).fetch(
            _ctx(linkedin_url="https://www.linkedin.com/company/does-not-exist")
        )
    )
    assert ev["linkedin_presence"].normalized_value == "not_found"
    assert set(ev) == {"linkedin_presence"}


def test_unconfigured_provider_is_unavailable():
    result = LinkedInAdapter(provider=UnconfiguredLinkedInProvider()).fetch(_ctx())
    assert isinstance(result, AdapterFailure)
    assert result.kind == "unavailable"


def test_provider_from_env(monkeypatch):
    monkeypatch.delenv("ENTITYIQ_LINKEDIN_PROVIDER", raising=False)
    assert isinstance(provider_from_env(), UnconfiguredLinkedInProvider)
    monkeypatch.setenv("ENTITYIQ_LINKEDIN_PROVIDER", "stub")
    assert isinstance(provider_from_env(), StubLinkedInProvider)
    monkeypatch.setenv("ENTITYIQ_LINKEDIN_PROVIDER", "scrape")
    with pytest.raises(ValueError):
        provider_from_env()


class _Raises:
    name = "raising"

    def __init__(self, exc):
        self._exc = exc

    def by_url(self, url):
        raise self._exc

    def search(self, company_name, domain):
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
    result = LinkedInAdapter(provider=_Raises(exc)).fetch(_ctx())
    assert isinstance(result, AdapterFailure)
    assert result.kind == kind


def test_adapter_module_has_no_http_client():
    """PRD §6: scraping is out of scope, so the adapter never fetches pages."""
    import app.adapters.linkedin as mod

    source = open(mod.__file__).read()
    for forbidden in ("httpx", "requests", "playwright", "urllib"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# Pipeline stage + normalization
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
        work_email="jane@acme.com",
        country="US",
        linkedin_url="https://www.linkedin.com/company/acme-corp",
        requester_full_name="Jane Smith",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(submission_id=sub.id, entity_id=entity.id, status="pending")
    db.add(run)
    db.commit()
    return run.id


def test_normalize_passes_linkedin_url_and_requester_through(db):
    from app.pipeline.normalize import NormalizeInputStage

    run_id = _run(db)
    normalized = NormalizeInputStage().run(run_id, db, {})["normalized"]
    assert normalized["linkedin_url"] == "https://www.linkedin.com/company/acme-corp"
    assert normalized["requester_full_name"] == "Jane Smith"


def test_stage_persists_evidence_and_preserves_context(db):
    run_id = _run(db)
    out = VerifyLinkedInStage(LinkedInAdapter(provider=_provider())).run(
        run_id,
        db,
        {
            "prior": "kept",
            "normalized": {
                "company_name": "Acme Corporation",
                "domain": "acme.com",
                "linkedin_url": "https://www.linkedin.com/company/acme-corp",
                "requester_full_name": "Jane Smith",
            },
        },
    )
    assert out["prior"] == "kept"
    assert out["linkedin"]["status"] == "complete"
    fields = {
        e.field
        for e in db.query(Evidence).filter(Evidence.verification_run_id == run_id)
    }
    assert {"linkedin_presence", "linkedin_requester_match"} <= fields


def test_stage_is_registered_after_web_evidence():
    names = [s.name for s in default_stages()]
    assert names.index("verify_linkedin") == names.index("web_evidence") + 1
