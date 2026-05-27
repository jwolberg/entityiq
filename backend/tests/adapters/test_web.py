"""Tests for P2-T4 — Tier-3 public web evidence adapter.

All tests are fully offline — a FakeWebFetcher is injected; no real network
calls are made and Playwright is never imported.

Test scenarios (per ticket AC):
  - Site with content → branding (web_brand) + contact evidence with
    attribution (emails, phones, addresses).
  - Thin/templated site (no emails, no contacts) → web_thin_footprint signal.
  - Site unreachable → typed AdapterFailure(kind="unavailable"); run continues.
  - Pipeline stage: WebEvidenceStage persists evidence and returns context.
  - No domain in context → AdapterFailure(kind="not_found").
  - HTTP 4xx from site → typed AdapterFailure(kind="unavailable").
  - Playwright is NOT imported at the module level (verified below).
"""

from __future__ import annotations

import importlib
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.base import AdapterContext, AdapterFailure, AdapterSuccess
from app.adapters.web import (
    FetchResult,
    WebAdapter,
    WebEvidenceStage,
    _extract_h1,
    _extract_meta,
    _extract_title,
    _strip_tags,
)
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

# ---------------------------------------------------------------------------
# Playwright isolation check
# ---------------------------------------------------------------------------


def test_playwright_not_imported_at_module_level() -> None:
    """Importing web.py must NOT import playwright.

    This test ensures CI environments without browsers can import the module.
    """
    # If playwright were imported at module level, it would appear in
    # sys.modules after importing app.adapters.web.
    # We re-import to be sure (module is already imported by the imports above).
    importlib.import_module("app.adapters.web")
    assert (
        "playwright" not in sys.modules
    ), "playwright must NOT be imported at module level in app/adapters/web.py"


# ---------------------------------------------------------------------------
# HTML fixture helpers
# ---------------------------------------------------------------------------

_RICH_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Acme Corporation — Home</title>
  <meta property="og:site_name" content="Acme Corp" />
</head>
<body>
  <h1>Welcome to Acme Corporation</h1>
  <p>Contact us at info@acme.com or sales@acme.com</p>
  <p>Phone: +1 (415) 555-0100</p>
  <p>Address: 123 Market Street, San Francisco, CA</p>
  <p>For press inquiries: press@acme.com</p>
</body>
</html>"""

_THIN_HTML = """<!DOCTYPE html>
<html>
<head><title>Under Construction</title></head>
<body>
  <h1>Coming Soon</h1>
  <p>This site is under construction.</p>
</body>
</html>"""

_MINIMAL_HTML = """<html><body><p>Hello world</p></body></html>"""


# ---------------------------------------------------------------------------
# Fake web fetcher helpers
# ---------------------------------------------------------------------------


class _FakeWebFetcher:
    """Returns a pre-configured FetchResult."""

    def __init__(
        self,
        text: str = _RICH_HTML,
        status_code: int = 200,
        url: str = "https://acme.com",
    ) -> None:
        self._result = FetchResult(url=url, text=text, status_code=status_code)

    def fetch(self, url: str) -> FetchResult:
        return self._result


class _UnreachableFetcher:
    """Always raises a connection error."""

    def fetch(self, url: str) -> FetchResult:
        raise ConnectionError("Connection refused")


class _TimeoutFetcher:
    """Raises a TimeoutError."""

    def fetch(self, url: str) -> FetchResult:
        raise TimeoutError("Request timed out")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    run_id: str = "run-t4-test",
    domain: str = "acme.com",
    company_name: str = "Acme Corporation",
) -> AdapterContext:
    return AdapterContext(
        run_id=run_id,
        company_name=company_name,
        domain=domain,
        country_iso="US",
    )


# ---------------------------------------------------------------------------
# Unit tests: WebAdapter — site with content
# ---------------------------------------------------------------------------


class TestWebAdapterRichSite:
    """Site with branding + contacts → evidence rows with attribution."""

    def test_returns_adapter_success(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher())
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)

    def test_branding_evidence_emitted(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher())
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "web_brand" in fields

    def test_email_contact_evidence_emitted(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher())
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "web_contacts_email" in fields

    def test_phone_contact_evidence_emitted(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher())
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "web_contacts_phone" in fields

    def test_all_evidence_has_attribution(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher())
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)
        for ev in result.evidence:
            assert ev.attribution is not None
            assert ev.source == "web"
            assert ev.tier == 3

    def test_brand_from_og_site_name(self) -> None:
        """og:site_name takes priority over title."""
        adapter = WebAdapter(fetcher=_FakeWebFetcher(text=_RICH_HTML))
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)
        brand_evs = [ev for ev in result.evidence if ev.field == "web_brand"]
        assert len(brand_evs) >= 1
        assert brand_evs[0].raw_value == "Acme Corp"

    def test_employee_footprint_evidence_emitted(self) -> None:
        """Multiple emails → web_employee_footprint evidence."""
        adapter = WebAdapter(fetcher=_FakeWebFetcher())
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "web_employee_footprint" in fields
        assert "web_thin_footprint" not in fields


class TestWebAdapterThinSite:
    """Thin/templated site → low employee-footprint signal."""

    def test_thin_site_returns_success(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher(text=_THIN_HTML))
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)

    def test_thin_footprint_signal_emitted(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher(text=_THIN_HTML))
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "web_thin_footprint" in fields

    def test_no_email_evidence_on_thin_site(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher(text=_THIN_HTML))
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "web_contacts_email" not in fields


class TestWebAdapterUnreachable:
    """Unreachable site → typed AdapterFailure; run continues."""

    def test_connection_error_returns_unavailable(self) -> None:
        adapter = WebAdapter(fetcher=_UnreachableFetcher())
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterFailure)
        assert result.kind == "unavailable"

    def test_timeout_returns_timeout_failure(self) -> None:
        adapter = WebAdapter(fetcher=_TimeoutFetcher())
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterFailure)
        assert result.kind == "timeout"

    def test_http_error_returns_unavailable(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher(text="", status_code=404))
        result = adapter.fetch(_make_context())
        assert isinstance(result, AdapterFailure)
        assert result.kind == "unavailable"

    def test_unavailable_does_not_raise(self) -> None:
        """Adapter must not propagate exceptions."""
        adapter = WebAdapter(fetcher=_UnreachableFetcher())
        result = adapter.fetch(_make_context())
        assert result is not None


class TestWebAdapterNoDomain:
    """No domain in context → not_found failure."""

    def test_no_domain_returns_not_found(self) -> None:
        adapter = WebAdapter(fetcher=_FakeWebFetcher())
        ctx = AdapterContext(run_id="run-t4", company_name="Acme")
        result = adapter.fetch(ctx)
        assert isinstance(result, AdapterFailure)
        assert result.kind == "not_found"


# ---------------------------------------------------------------------------
# Unit tests: HTML parsing helpers
# ---------------------------------------------------------------------------


class TestExtractMeta:
    def test_extracts_og_site_name(self) -> None:
        html = '<meta property="og:site_name" content="My Company" />'
        assert _extract_meta(html, "og:site_name") == "My Company"

    def test_returns_none_if_absent(self) -> None:
        html = "<html><head></head></html>"
        assert _extract_meta(html, "og:site_name") is None

    def test_case_insensitive(self) -> None:
        html = '<META PROPERTY="og:site_name" CONTENT="TestCo" />'
        assert _extract_meta(html, "og:site_name") == "TestCo"


class TestExtractTitle:
    def test_extracts_title(self) -> None:
        html = "<title>Hello World</title>"
        assert _extract_title(html) == "Hello World"

    def test_returns_none_if_absent(self) -> None:
        assert _extract_title("<html></html>") is None


class TestExtractH1:
    def test_extracts_h1(self) -> None:
        html = "<h1>Welcome</h1>"
        assert _extract_h1(html) == "Welcome"

    def test_returns_none_if_absent(self) -> None:
        assert _extract_h1("<html><body></body></html>") is None


class TestStripTags:
    def test_removes_tags(self) -> None:
        assert "hello" in _strip_tags("<p>hello</p>")

    def test_decodes_amp(self) -> None:
        assert "&" in _strip_tags("A &amp; B")


# ---------------------------------------------------------------------------
# Pipeline stage tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def db_run(db_session: Session) -> VerificationRun:
    entity = Entity(canonical_name="Acme Corporation", canonical_domain="acme.com")
    db_session.add(entity)
    db_session.flush()

    submission = Submission(
        company_name="Acme Corporation",
        domain="acme.com",
        work_email="ceo@acme.com",
        country="US",
        entity_id=entity.id,
    )
    db_session.add(submission)
    db_session.flush()

    run = VerificationRun(
        submission_id=submission.id,
        entity_id=entity.id,
        status="running",
    )
    db_session.add(run)
    db_session.commit()
    return run


class TestWebEvidenceStage:
    def test_stage_persists_evidence_on_rich_site(
        self, db_session: Session, db_run: VerificationRun
    ) -> None:
        stage = WebEvidenceStage(fetcher=_FakeWebFetcher())
        context = {"normalized": {"domain": "acme.com", "company_name": "Acme"}}
        new_ctx = stage.run(db_run.id, db_session, context)

        assert new_ctx["web_evidence"]["status"] == "complete"
        evs = (
            db_session.query(Evidence)
            .filter(Evidence.verification_run_id == db_run.id)
            .all()
        )
        assert len(evs) > 0
        fields = {ev.field for ev in evs}
        assert "web_brand" in fields

    def test_stage_handles_unreachable_gracefully(
        self, db_session: Session, db_run: VerificationRun
    ) -> None:
        stage = WebEvidenceStage(fetcher=_UnreachableFetcher())
        context = {"normalized": {"domain": "gone.com"}}
        new_ctx = stage.run(db_run.id, db_session, context)

        assert new_ctx["web_evidence"]["status"] == "unavailable"

    def test_stage_returns_updated_context(
        self, db_session: Session, db_run: VerificationRun
    ) -> None:
        stage = WebEvidenceStage(fetcher=_FakeWebFetcher())
        context = {
            "normalized": {"domain": "acme.com"},
            "existing_key": "preserved",
        }
        new_ctx = stage.run(db_run.id, db_session, context)

        assert "existing_key" in new_ctx
        assert new_ctx["existing_key"] == "preserved"
        assert "web_evidence" in new_ctx

    def test_stage_no_domain_records_not_found(
        self, db_session: Session, db_run: VerificationRun
    ) -> None:
        stage = WebEvidenceStage(fetcher=_FakeWebFetcher())
        context = {"normalized": {"company_name": "Acme"}}
        new_ctx = stage.run(db_run.id, db_session, context)

        assert new_ctx["web_evidence"]["status"] == "not_found"
