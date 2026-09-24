"""Tests for P1-T7 — Risk scoring v1 + report assembly.

All tests run against SQLite in-memory.  No live Redis, Postgres, or broker.

Test scenarios:
  1. Strong registry + long-lived domain → low risk; signals cite evidence
  2. Registry mismatch + fresh domain → elevated risk; named signals
  3. Missing/unavailable sources → reduced confidence, NOT defaulted to low risk
  4. INTEGRATION: every signal references ≥1 evidence row (no orphan signals)
  5. pre_clear triage tier carries no auto-approval
  6. StoreReportStage creates a Report row from scored run
  7. Report assembled mid-run (partial) is readable
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all ORM models
from app.db.session import Base
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.field_comparison import FieldComparison
from app.models.report import Report
from app.models.risk_assessment import RiskAssessment
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.scoring.engine import (
    TRIAGE_PRE_CLEAR_MAX,
    ScoringEngine,
    ScoringStage,
    _triage_tier,
)
from app.scoring.report import StoreReportStage, assemble_report

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scoring_engine():
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
def db(scoring_engine):
    connection = scoring_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helpers to build evidence / comparison rows in-memory
# ---------------------------------------------------------------------------


def _make_ev(
    run_id: str,
    source: str,
    tier: int,
    field: str,
    raw_value: str | None = None,
    normalized_value: str | None = None,
    confidence: float = 0.85,
) -> Evidence:
    return Evidence(
        verification_run_id=run_id,
        source=source,
        tier=tier,
        field=field,
        raw_value=raw_value,
        normalized_value=(
            normalized_value if normalized_value is not None else raw_value
        ),
        confidence=confidence,
        fetched_at=datetime.now(tz=timezone.utc),
    )


def _make_fc(
    run_id: str,
    field_name: str,
    submitted: str | None,
    discovered: str | None,
    status: str,
    evidence_id: str | None = None,
) -> FieldComparison:
    return FieldComparison(
        verification_run_id=run_id,
        field_name=field_name,
        submitted_value=submitted,
        discovered_value=discovered,
        match_status=status,
        evidence_id=evidence_id,
    )


def _make_run(db: Session) -> tuple[str, str]:
    """Create Entity + Submission + VerificationRun; return (entity_id, run_id)."""
    entity = Entity(canonical_name="Test Corp", canonical_domain="testcorp.example")
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name="Test Corp",
        domain="testcorp.example",
        work_email="ceo@testcorp.example",
        country="US",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()

    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="running",
        source_availability={},
    )
    db.add(run)
    db.commit()
    return entity.id, run.id


# ---------------------------------------------------------------------------
# Unit tests: ScoringEngine (pure — no DB)
# ---------------------------------------------------------------------------


class TestLowRiskScenario:
    """Strong registry match + long-lived domain → low overall risk."""

    def test_overall_score_is_low(self):
        run_id = str(uuid.uuid4())
        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "Test Corp")
        ev_name.id = str(uuid.uuid4())
        ev_reg = _make_ev(run_id, "opencorporates", 1, "registration_number", "12345")
        ev_reg.id = str(uuid.uuid4())
        ev_status = _make_ev(
            run_id, "opencorporates", 1, "registration_status", "Active"
        )
        ev_status.id = str(uuid.uuid4())
        ev_age = _make_ev(
            run_id, "whois", 2, "domain_age_days", "1500", normalized_value="1500"
        )
        ev_age.id = str(uuid.uuid4())
        ev_mx = _make_ev(
            run_id, "dns", 2, "mx_present", "true", normalized_value="true"
        )
        ev_mx.id = str(uuid.uuid4())

        fc_name = _make_fc(
            run_id, "company_name", "Test Corp", "Test Corp", "match", ev_name.id
        )
        fc_country = _make_fc(run_id, "country_iso", "US", "US", "match", ev_status.id)

        evidence = [ev_name, ev_reg, ev_status, ev_age, ev_mx]
        comparisons = [fc_name, fc_country]

        result = ScoringEngine().score(evidence, comparisons)

        assert result.overall_score < 35, (
            f"Expected low risk (< 35) for confirmed entity + established domain, "
            f"got {result.overall_score}"
        )

    def test_contributing_signals_cite_evidence(self):
        run_id = str(uuid.uuid4())
        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "Test Corp")
        ev_name.id = str(uuid.uuid4())
        ev_age = _make_ev(
            run_id, "whois", 2, "domain_age_days", "1500", normalized_value="1500"
        )
        ev_age.id = str(uuid.uuid4())

        evidence = [ev_name, ev_age]
        comparisons = []

        result = ScoringEngine().score(evidence, comparisons)

        # Every signal that has evidence_ids must reference existing evidence
        evidence_id_set = {e.id for e in evidence}
        for sig in result.contributing_signals:
            for ev_id in sig.evidence_ids:
                assert (
                    ev_id in evidence_id_set
                ), f"Signal {sig.name!r} references unknown evidence_id {ev_id!r}"

    def test_triage_tier_is_pre_clear_or_review(self):
        run_id = str(uuid.uuid4())
        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "Test Corp")
        ev_name.id = str(uuid.uuid4())
        ev_reg = _make_ev(run_id, "opencorporates", 1, "registration_number", "ABC")
        ev_reg.id = str(uuid.uuid4())
        ev_age = _make_ev(
            run_id, "whois", 2, "domain_age_days", "2000", normalized_value="2000"
        )
        ev_age.id = str(uuid.uuid4())
        ev_mx = _make_ev(
            run_id, "dns", 2, "mx_present", "true", normalized_value="true"
        )
        ev_mx.id = str(uuid.uuid4())

        fc_name = _make_fc(
            run_id, "company_name", "Test Corp", "Test Corp", "match", ev_name.id
        )

        result = ScoringEngine().score([ev_name, ev_reg, ev_age, ev_mx], [fc_name])
        assert result.triage_tier in ("pre_clear", "review"), (
            "Expected pre_clear or review for confirmed entity, "
            f"got {result.triage_tier}"
        )


class TestElevatedRiskScenario:
    """Registry mismatch + recently registered domain → elevated risk."""

    def test_overall_score_is_elevated(self):
        run_id = str(uuid.uuid4())
        # Tier-1: name found but it's a mismatch via field comparison
        ev_name = _make_ev(
            run_id, "opencorporates", 1, "company_name", "Different Corp"
        )
        ev_name.id = str(uuid.uuid4())
        # Tier-2: recently registered + no MX
        ev_recent = _make_ev(
            run_id, "whois", 2, "recently_registered", "true", normalized_value="true"
        )
        ev_recent.id = str(uuid.uuid4())
        ev_no_mx = _make_ev(run_id, "dns", 2, "no_mx", "true", normalized_value="true")
        ev_no_mx.id = str(uuid.uuid4())

        # Field comparison: company name mismatch
        fc_name = _make_fc(
            run_id,
            "company_name",
            "Test Corp",
            "Different Corp",
            "mismatch",
            ev_name.id,
        )

        evidence = [ev_name, ev_recent, ev_no_mx]
        comparisons = [fc_name]

        result = ScoringEngine().score(evidence, comparisons)

        assert result.overall_score > 55, (
            f"Expected elevated risk (> 55) for mismatch + fresh domain, "
            f"got {result.overall_score}"
        )

    def test_elevated_risk_has_named_signals(self):
        run_id = str(uuid.uuid4())
        ev_recent = _make_ev(
            run_id, "whois", 2, "recently_registered", "true", normalized_value="true"
        )
        ev_recent.id = str(uuid.uuid4())
        ev_no_mx = _make_ev(run_id, "dns", 2, "no_mx", "true", normalized_value="true")
        ev_no_mx.id = str(uuid.uuid4())

        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "Other Corp")
        ev_name.id = str(uuid.uuid4())
        fc_name = _make_fc(
            run_id, "company_name", "Test Corp", "Other Corp", "mismatch", ev_name.id
        )

        result = ScoringEngine().score([ev_recent, ev_no_mx, ev_name], [fc_name])

        signal_names = {sig.name for sig in result.contributing_signals}
        # At least one elevated signal should be present and named
        elevated = [s for s in result.contributing_signals if s.direction == "elevated"]
        assert len(elevated) >= 1, "Expected at least one elevated signal"
        assert any(
            "recently_registered" in n or "no_mx" in n or "mismatch" in n
            for n in signal_names
        ), f"Expected named signals, got: {signal_names}"

    def test_triage_tier_is_review_or_escalate(self):
        run_id = str(uuid.uuid4())
        ev_recent = _make_ev(
            run_id, "whois", 2, "recently_registered", "true", normalized_value="true"
        )
        ev_recent.id = str(uuid.uuid4())
        ev_no_mx = _make_ev(run_id, "dns", 2, "no_mx", "true", normalized_value="true")
        ev_no_mx.id = str(uuid.uuid4())
        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "Other Corp")
        ev_name.id = str(uuid.uuid4())
        fc_name = _make_fc(
            run_id, "company_name", "Test Corp", "Other Corp", "mismatch", ev_name.id
        )

        result = ScoringEngine().score([ev_recent, ev_no_mx, ev_name], [fc_name])
        assert result.triage_tier in (
            "review",
            "escalate",
        ), f"Expected review or escalate for elevated risk, got {result.triage_tier}"


class TestMissingSourcesScenario:
    """Missing/unavailable sources → reduced confidence, NOT defaulted to low risk."""

    def test_no_evidence_gives_reduced_confidence(self):
        result = ScoringEngine().score([], [])
        assert (
            result.confidence == 0.0
        ), f"Expected 0.0 confidence with no evidence, got {result.confidence}"

    def test_no_evidence_score_is_not_low_risk(self):
        """No evidence must NOT default to low risk — we simply don't know."""
        result = ScoringEngine().score([], [])
        # Score should be in the uncertain/elevated range (> 30), not pre-cleared
        assert (
            result.overall_score > 30
        ), f"No evidence must not default to low risk, got {result.overall_score}"

    def test_partial_sources_reduce_confidence_not_increase_score(self):
        """Only Tier-2 available (no Tier-1): confidence < 1.0, score uncertain."""
        run_id = str(uuid.uuid4())
        ev_age = _make_ev(
            run_id, "whois", 2, "domain_age_days", "1500", normalized_value="1500"
        )
        ev_age.id = str(uuid.uuid4())
        ev_mx = _make_ev(
            run_id, "dns", 2, "mx_present", "true", normalized_value="true"
        )
        ev_mx.id = str(uuid.uuid4())

        result = ScoringEngine().score([ev_age, ev_mx], [])

        # Only Tier-2: 1 of 3 tiers = 0.33 confidence
        assert result.confidence < 1.0
        # No Tier-1 evidence should NOT produce a perfect (0) low-risk score
        assert (
            result.entity_score > 0
        ), "Missing Tier-1 should not produce perfect entity legitimacy score"

    def test_unavailable_sources_described_in_signals(self):
        """Unavailable infrastructure data should be reflected in signals."""
        result = ScoringEngine().score([], [])
        # With no evidence there should be signals that explain the uncertainty
        assert (
            len(result.contributing_signals) > 0
        ), "Expected signals even when no evidence (to explain uncertainty)"


class TestIntegrationNoOrphanSignals:
    """INTEGRATION: every signal that references evidence IDs references valid rows."""

    def test_all_signal_evidence_ids_reference_persisted_evidence(self, db: Session):
        """End-to-end: ScoringStage persists RiskAssessment; all evidence_ids valid."""
        _, run_id = _make_run(db)

        # Create evidence rows (persisted)
        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "TestCo")
        ev_age = _make_ev(
            run_id, "whois", 2, "domain_age_days", "1500", normalized_value="1500"
        )
        ev_mx = _make_ev(
            run_id, "dns", 2, "mx_present", "true", normalized_value="true"
        )
        db.add_all([ev_name, ev_age, ev_mx])
        db.commit()

        # Field comparisons
        fc = FieldComparison(
            verification_run_id=run_id,
            field_name="company_name",
            submitted_value="TestCo",
            discovered_value="TestCo",
            match_status="match",
            evidence_id=ev_name.id,
        )
        db.add(fc)
        db.commit()

        # Run the scoring stage
        stage = ScoringStage()
        stage.run(run_id, db, {})

        # Verify: RiskAssessment persisted
        assessment = (
            db.query(RiskAssessment)
            .filter(RiskAssessment.verification_run_id == run_id)
            .first()
        )
        assert assessment is not None

        # Verify: all evidence_ids in signals reference real Evidence rows
        all_ev_ids = {
            e.id
            for e in db.query(Evidence)
            .filter(Evidence.verification_run_id == run_id)
            .all()
        }

        for sig_dict in assessment.contributing_signals or []:
            for ev_id in sig_dict.get("evidence_ids", []):
                assert ev_id in all_ev_ids, (
                    f"Signal {sig_dict['name']!r} references "
                    f"orphan evidence_id {ev_id!r}"
                )

    def test_assessment_evidence_items_linked(self, db: Session):
        """Association table: assessment.evidence_items contains linked rows."""
        _, run_id = _make_run(db)

        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "TestCo")
        ev_age = _make_ev(
            run_id, "whois", 2, "domain_age_days", "1500", normalized_value="1500"
        )
        db.add_all([ev_name, ev_age])
        db.commit()

        stage = ScoringStage()
        stage.run(run_id, db, {})

        assessment = (
            db.query(RiskAssessment)
            .filter(RiskAssessment.verification_run_id == run_id)
            .first()
        )
        assert assessment is not None
        # At least one evidence item should be linked via association table
        assert (
            len(assessment.evidence_items) >= 1
        ), "Expected at least one evidence item linked via risk_assessment_evidence"


class TestNoAutoApproval:
    """PRD Non-Goals invariant: pre_clear carries no auto-approval."""

    def test_pre_clear_is_triage_signal_only(self):
        """A pre_clear outcome has no 'approved' or 'decision' field."""
        result = ScoringEngine().score(
            # Strong trust evidence
            [
                _make_ev_with_id("opencorporates", 1, "company_name", "X Corp"),
                _make_ev_with_id(
                    "whois", 2, "domain_age_days", "2000", normalized_value="2000"
                ),
                _make_ev_with_id(
                    "dns", 2, "mx_present", "true", normalized_value="true"
                ),
            ],
            [],
        )
        # If score is low enough to be pre_clear, assert no decision attribute
        if result.triage_tier == "pre_clear":
            # ScoringResult has no 'decision' or 'approved' field
            assert not hasattr(result, "decision")
            assert not hasattr(result, "approved")
            assert not hasattr(result, "rejected")
            # The tier itself must NOT be interpreted as an approval
            assert result.triage_tier == "pre_clear"
            assert result.triage_tier != "approved"

    def test_triage_threshold_constants_are_advisory(self):
        """Triage thresholds map to tier names — none named 'approved'/'rejected'."""
        assert _triage_tier(TRIAGE_PRE_CLEAR_MAX) == "pre_clear"
        assert _triage_tier(100) == "escalate"
        assert _triage_tier(50) == "review"
        # None of these are 'approved' or 'rejected'
        for score in range(0, 101, 10):
            tier = _triage_tier(float(score))
            assert tier not in ("approved", "rejected", "auto_approved"), (
                f"Triage tier must never be 'approved'/'rejected' — got {tier!r} "
                f"for score {score}"
            )


class TestStoreReport:
    """StoreReportStage creates/updates Report rows."""

    def test_completed_run_produces_full_report(self, db: Session):
        _, run_id = _make_run(db)

        # Populate evidence + field comparison
        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "Acme Inc")
        ev_age = _make_ev(
            run_id, "whois", 2, "domain_age_days", "1500", normalized_value="1500"
        )
        db.add_all([ev_name, ev_age])
        db.commit()

        fc = FieldComparison(
            verification_run_id=run_id,
            field_name="company_name",
            submitted_value="Acme Inc",
            discovered_value="Acme Inc",
            match_status="match",
            evidence_id=ev_name.id,
        )
        db.add(fc)
        db.commit()

        # Mark stages complete in source_availability
        run = db.get(VerificationRun, run_id)
        run.source_availability = {
            "query_registries": "complete",
            "analyze_domain": "complete",
            "consistency_checks": "complete",
            "scoring": "complete",
        }
        db.commit()

        # Run scoring stage first
        ScoringStage().run(run_id, db, {})

        # Run store report stage
        StoreReportStage().run(run_id, db, {})

        report = db.query(Report).filter(Report.verification_run_id == run_id).first()
        assert report is not None
        assert report.status in ("complete", "partial")
        summary = report.summary or {}
        assert "scores" in summary
        assert summary["scores"] is not None
        assert "evidence" in summary
        assert len(summary["evidence"]) >= 1
        assert "mismatches" in summary
        assert "sources" in summary

    def test_in_progress_run_produces_partial_report(self, db: Session):
        _, run_id = _make_run(db)

        # Only partial stages complete
        run = db.get(VerificationRun, run_id)
        run.source_availability = {
            "query_registries": "complete",
            "analyze_domain": "pending",
            "consistency_checks": "pending",
            "scoring": "pending",
        }
        db.commit()

        ev_name = _make_ev(run_id, "opencorporates", 1, "company_name", "Acme Inc")
        db.add(ev_name)
        db.commit()

        assemble_report(run_id, db)

        report = db.query(Report).filter(Report.verification_run_id == run_id).first()
        assert report is not None
        assert report.status == "partial"
        assert report.section_statuses is not None
        assert report.section_statuses.get("scores") == "pending"
        assert report.section_statuses.get("evidence") == "complete"


# ---------------------------------------------------------------------------
# Helper used in TestNoAutoApproval
# ---------------------------------------------------------------------------


def _make_ev_with_id(
    source: str,
    tier: int,
    field: str,
    raw_value: str | None = None,
    normalized_value: str | None = None,
) -> Evidence:
    run_id = str(uuid.uuid4())
    ev = Evidence(
        verification_run_id=run_id,
        source=source,
        tier=tier,
        field=field,
        raw_value=raw_value,
        normalized_value=(
            normalized_value if normalized_value is not None else raw_value
        ),
        confidence=0.9,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    ev.id = str(uuid.uuid4())
    return ev


# ---------------------------------------------------------------------------
# P5-T3 finding — critical signals must drive the STORED triage tier
# ---------------------------------------------------------------------------


def test_sanctions_hit_escalates_even_when_everything_else_is_clean():
    """A sanctions match on an otherwise pristine company used to be stored as
    pre_clear (score-only tier) while the report summary said escalate."""
    from app.models.evidence import Evidence
    from app.scoring.engine import ScoringEngine

    def ev(source, tier, field, value):
        e = Evidence(
            verification_run_id="run-x",
            source=source,
            tier=tier,
            field=field,
            raw_value=value,
            normalized_value=value,
            confidence=0.95,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        e.id = str(uuid.uuid4())
        return e

    evidence = [
        ev("sanctions", 1, "sanctions_hit", "VOLGA MARITIME TRADING"),
        ev("opencorporates", 1, "company_name", "Volga Maritime Trading Ltd"),
        ev("domain", 2, "domain_age_days", "4000"),
        ev("domain", 2, "mx_present", "true"),
        ev("domain", 2, "spf_present", "true"),
        ev("domain", 2, "ssl_issuer", "DigiCert"),
    ]
    result = ScoringEngine().score(evidence, [])
    assert result.overall_score < 70  # the score alone would not escalate...
    assert result.triage_tier == "escalate"  # ...the sanctions hit must override
