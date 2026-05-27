"""Tests for P2-T3 — Sanctions/watchlist screening via public OFAC SDN list.

All tests are offline — a fake SDN list fetcher is injected; the live OFAC
URL is never called.

Test scenarios (per ticket AC):
  - Known sanctioned name (fixture) → hit + sanctions_risk_flag evidence.
  - Clean name → no hit, sanctions_screened evidence row emitted.
  - List unavailable → typed AdapterFailure(kind="unavailable"); run continues.
  - Name normalization:
      - Exact case-insensitive match.
      - Legal-suffix stripping (e.g. "ACME Corp" matches "ACME" in list).
  - Pipeline stage: SanctionsScreeningStage persists evidence and returns context.
  - No company_name in context → AdapterFailure(kind="not_found").
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.adapters.base import AdapterContext, AdapterFailure, AdapterSuccess
from app.adapters.sanctions import (
    SanctionsAdapter,
    SanctionsScreeningStage,
    _normalize_name,
    _parse_sdn_csv,
)
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

# ---------------------------------------------------------------------------
# Fixture SDN CSV — small deterministic list for tests
# ---------------------------------------------------------------------------

# sdn.csv format: ent_num, sdn_name, sdn_type, program, ...
# No header row in the real file.
_FIXTURE_SDN_CSV = """\
1000,EVIL CORP,entity,SDGT,,,,,,,,
1001,SANCTIONED ENTITY LTD,entity,IRAN,,,,,,,,
1002,BAD ACTOR LLC,entity,SDGT,,,,,,,,
1003,TOTALLY FINE COMPANY,entity,OTHER,,,,,,,,
"""

# Name that IS in the fixture list (stripped of legal suffix).
_HIT_NAME = "EVIL CORP"
# Name with legal suffix that normalizes to a list entry.
_HIT_NAME_WITH_SUFFIX = "Sanctioned Entity Ltd"
# Name that is NOT in the fixture list.
_CLEAN_NAME = "Legitimate Business Inc"
# Name matching after case normalization.
_HIT_NAME_LOWER = "evil corp"


# ---------------------------------------------------------------------------
# Fake fetcher helpers
# ---------------------------------------------------------------------------


class _FixtureFetcher:
    """Returns the small deterministic SDN CSV fixture."""

    def fetch_csv(self) -> str:
        return _FIXTURE_SDN_CSV


class _UnavailableFetcher:
    """Always raises RuntimeError — simulates an unavailable list."""

    def fetch_csv(self) -> str:
        raise RuntimeError("OFAC list unavailable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    run_id: str = "run-t3-test",
    company_name: str | None = "Test Company",
) -> AdapterContext:
    return AdapterContext(
        run_id=run_id,
        company_name=company_name,
        domain="testco.com",
        country_iso="US",
    )


# ---------------------------------------------------------------------------
# Unit tests: SanctionsAdapter
# ---------------------------------------------------------------------------


class TestSanctionsAdapterHit:
    """Known sanctioned name → hit evidence + risk flag."""

    def test_exact_match_returns_hit_evidence(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_HIT_NAME)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "sanctions_hit" in fields

    def test_hit_includes_risk_flag(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_HIT_NAME)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        fields = [ev.field for ev in result.evidence]
        assert "sanctions_risk_flag" in fields

    def test_hit_evidence_has_high_confidence(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_HIT_NAME)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        hit_evs = [ev for ev in result.evidence if ev.field == "sanctions_hit"]
        assert all(ev.confidence >= 0.9 for ev in hit_evs)

    def test_hit_evidence_has_attribution(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_HIT_NAME)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        hit_evs = [ev for ev in result.evidence if ev.field == "sanctions_hit"]
        assert len(hit_evs) > 0
        for ev in hit_evs:
            assert ev.attribution is not None
            assert ev.source == "sanctions"
            assert ev.tier == 1

    def test_no_sanctions_screened_on_hit(self) -> None:
        """sanctions_screened should NOT be emitted when there is a hit."""
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_HIT_NAME)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        fields = [ev.field for ev in result.evidence]
        assert "sanctions_screened" not in fields


class TestSanctionsAdapterClean:
    """Clean name → sanctions_screened evidence, no hit."""

    def test_clean_name_returns_screened_evidence(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_CLEAN_NAME)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "sanctions_screened" in fields

    def test_clean_name_no_hit_evidence(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_CLEAN_NAME)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "sanctions_hit" not in fields
        assert "sanctions_risk_flag" not in fields

    def test_screened_evidence_high_confidence(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_CLEAN_NAME)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        screened_evs = [
            ev for ev in result.evidence if ev.field == "sanctions_screened"
        ]
        assert len(screened_evs) == 1
        assert screened_evs[0].confidence == 1.0


class TestSanctionsAdapterUnavailable:
    """List unavailable → typed AdapterFailure; run continues."""

    def test_unavailable_fetcher_returns_failure(self) -> None:
        adapter = SanctionsAdapter(fetcher=_UnavailableFetcher())
        ctx = _make_context(company_name="Any Company")
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterFailure)
        assert result.kind == "unavailable"

    def test_unavailable_does_not_raise(self) -> None:
        """Adapter must not propagate exceptions — run must continue."""
        adapter = SanctionsAdapter(fetcher=_UnavailableFetcher())
        ctx = _make_context(company_name="Any Company")
        # Should return AdapterFailure, not raise.
        result = adapter.fetch(ctx)
        assert result is not None


class TestSanctionsAdapterNormalization:
    """Name normalization and fuzzy matching (suffix stripping, case)."""

    def test_case_insensitive_match(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_HIT_NAME_LOWER)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "sanctions_hit" in fields

    def test_legal_suffix_stripped_match(self) -> None:
        """'Sanctioned Entity Ltd' should match 'SANCTIONED ENTITY LTD' in fixture."""
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=_HIT_NAME_WITH_SUFFIX)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "sanctions_hit" in fields

    def test_suffix_stripped_clean_name_no_hit(self) -> None:
        """Suffix stripping should not cause false positives on clean names."""
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name="Legitimate Business Inc")
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterSuccess)
        fields = {ev.field for ev in result.evidence}
        assert "sanctions_hit" not in fields


class TestSanctionsAdapterNoName:
    """No company_name in context → not_found failure."""

    def test_no_company_name_returns_not_found(self) -> None:
        adapter = SanctionsAdapter(fetcher=_FixtureFetcher())
        ctx = _make_context(company_name=None)
        result = adapter.fetch(ctx)

        assert isinstance(result, AdapterFailure)
        assert result.kind == "not_found"


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestNormalizeName:
    def test_strips_ltd(self) -> None:
        assert _normalize_name("ACME Ltd") == _normalize_name("ACME")

    def test_strips_inc(self) -> None:
        assert _normalize_name("Foo Inc.") == _normalize_name("Foo")

    def test_case_insensitive(self) -> None:
        assert _normalize_name("EVIL CORP") == _normalize_name("evil corp")

    def test_strips_llc(self) -> None:
        assert _normalize_name("Bad Actor LLC") == _normalize_name("Bad Actor")


class TestParseSdnCsv:
    def test_parses_fixture_entries(self) -> None:
        entries = _parse_sdn_csv(_FIXTURE_SDN_CSV)
        names = [e["name"] for e in entries]
        assert "EVIL CORP" in names

    def test_skips_blank_rows(self) -> None:
        csv = "\n\n1000,EVIL CORP,entity,,,,\n\n"
        entries = _parse_sdn_csv(csv)
        assert len(entries) == 1

    def test_entries_have_id_and_name(self) -> None:
        entries = _parse_sdn_csv(_FIXTURE_SDN_CSV)
        for entry in entries:
            assert "id" in entry
            assert "name" in entry


# ---------------------------------------------------------------------------
# Pipeline stage test: SanctionsScreeningStage
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
    """Create a minimal VerificationRun row for stage tests."""
    entity = Entity(canonical_name="Test Company", canonical_domain="testco.com")
    db_session.add(entity)
    db_session.flush()

    submission = Submission(
        company_name="Test Company",
        domain="testco.com",
        work_email="test@testco.com",
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


class TestSanctionsScreeningStage:
    def test_stage_persists_screened_evidence_on_clean_name(
        self, db_session: Session, db_run: VerificationRun
    ) -> None:
        stage = SanctionsScreeningStage(fetcher=_FixtureFetcher())
        context = {"normalized": {"company_name": _CLEAN_NAME, "domain": "clean.com"}}
        new_ctx = stage.run(db_run.id, db_session, context)

        assert new_ctx["sanctions"]["status"] == "complete"
        evs = (
            db_session.query(Evidence)
            .filter(Evidence.verification_run_id == db_run.id)
            .all()
        )
        fields = {ev.field for ev in evs}
        assert "sanctions_screened" in fields

    def test_stage_persists_hit_evidence_on_sanctioned_name(
        self, db_session: Session, db_run: VerificationRun
    ) -> None:
        stage = SanctionsScreeningStage(fetcher=_FixtureFetcher())
        context = {"normalized": {"company_name": _HIT_NAME, "domain": "evil.com"}}
        new_ctx = stage.run(db_run.id, db_session, context)

        assert new_ctx["sanctions"]["status"] == "complete"
        evs = (
            db_session.query(Evidence)
            .filter(Evidence.verification_run_id == db_run.id)
            .all()
        )
        fields = {ev.field for ev in evs}
        assert "sanctions_hit" in fields
        assert "sanctions_risk_flag" in fields

    def test_stage_handles_unavailable_gracefully(
        self, db_session: Session, db_run: VerificationRun
    ) -> None:
        stage = SanctionsScreeningStage(fetcher=_UnavailableFetcher())
        context = {"normalized": {"company_name": "Any Co"}}
        new_ctx = stage.run(db_run.id, db_session, context)

        assert new_ctx["sanctions"]["status"] == "unavailable"
        # Run should continue — stage does not raise.

    def test_stage_returns_updated_context(
        self, db_session: Session, db_run: VerificationRun
    ) -> None:
        stage = SanctionsScreeningStage(fetcher=_FixtureFetcher())
        context = {
            "normalized": {"company_name": _CLEAN_NAME},
            "existing_key": "value",
        }
        new_ctx = stage.run(db_run.id, db_session, context)

        assert "existing_key" in new_ctx
        assert "sanctions" in new_ctx
