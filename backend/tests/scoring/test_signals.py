"""Tests for P2-T6 — Full four-layer scoring + signal catalog.

All tests run against SQLite in-memory (StaticPool).  No live Redis, Postgres,
or broker.

Test scenarios:
  1. Clean entity → low risk; signals cite evidence
  2. Sanctions hit → high entity risk + high fraud risk; named signals
  3. Registry mismatch → elevated representation risk; named signal
  4. Thin web + datacenter IP + fresh domain → stacking fraud layer score
  5. Reduced source coverage → lower confidence not defaulted-low-risk
  6. INTEGRATION: every layer score lists contributing signals, each tracing
     to ≥1 evidence row (no orphan signals)
  7. Cross-submission IP/ASN reuse detection (requires DB session)
  8. IP country mismatch → elevated representation signal
  9. Trust signals: long-lived domain, active footprint, stable web presence
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
from app.models.risk_assessment import RiskAssessment
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.scoring.engine import ScoringEngine, ScoringStage
from app.scoring.signals import (
    entity_legitimacy_signals,
    fraud_staging_risk_signals,
    infrastructure_legitimacy_signals,
    representation_confidence_signals,
)

# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def signals_engine():
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
def db(signals_engine):
    connection = signals_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ev(
    run_id: str,
    source: str,
    tier: int,
    field: str,
    raw: str | None = None,
    normalized: str | None = None,
    confidence: float = 0.9,
) -> Evidence:
    ev = Evidence(
        verification_run_id=run_id,
        source=source,
        tier=tier,
        field=field,
        raw_value=raw,
        normalized_value=normalized if normalized is not None else raw,
        confidence=confidence,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    ev.id = str(uuid.uuid4())
    return ev


def _fc(
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
# 1. Clean entity → low risk
# ---------------------------------------------------------------------------


class TestCleanEntity:
    """Clean entity with full evidence stack → low overall risk, trust signals."""

    def test_overall_score_is_low(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "123456"),
            _ev(
                run_id,
                "opencorporates",
                1,
                "registration_status",
                "Active",
                normalized="active",
            ),
            _ev(run_id, "sanctions", 1, "sanctions_screened", "no_hit"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "dns", 2, "mx_present", "true", normalized="true"),
            _ev(run_id, "dns", 2, "spf_present", "true", normalized="true"),
            _ev(run_id, "ssl", 2, "ssl_valid", "true"),
            _ev(run_id, "web", 3, "web_brand", "Test Corp"),
            _ev(run_id, "web", 3, "web_employee_footprint", "5"),
        ]
        comparisons = [
            _fc(
                run_id,
                "company_name",
                "Test Corp",
                "Test Corp",
                "match",
                evidence[0].id,
            ),
            _fc(run_id, "country_iso", "US", "US", "match", evidence[2].id),
        ]

        result = ScoringEngine().score(evidence, comparisons)

        assert (
            result.overall_score < 35
        ), f"Expected low risk (< 35) for clean entity, got {result.overall_score}"

    def test_trust_signals_present_and_cite_evidence(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "ABC"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "dns", 2, "mx_present", "true", normalized="true"),
        ]
        comparisons = [
            _fc(
                run_id,
                "company_name",
                "Test Corp",
                "Test Corp",
                "match",
                evidence[0].id,
            )
        ]

        result = ScoringEngine().score(evidence, comparisons)

        trust = [s for s in result.contributing_signals if s.direction == "trust"]
        assert len(trust) >= 2, "Expected multiple trust signals for clean entity"
        ev_id_set = {e.id for e in evidence}
        for sig in trust:
            for ev_id in sig.evidence_ids:
                assert (
                    ev_id in ev_id_set
                ), f"Signal {sig.name!r} references unknown evidence_id {ev_id!r}"

    def test_triage_tier_pre_clear_or_review(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "ABC"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "dns", 2, "mx_present", "true", normalized="true"),
            _ev(run_id, "dns", 2, "spf_present", "true", normalized="true"),
        ]
        comparisons = [
            _fc(
                run_id,
                "company_name",
                "Test Corp",
                "Test Corp",
                "match",
                evidence[0].id,
            )
        ]
        result = ScoringEngine().score(evidence, comparisons)
        assert result.triage_tier in (
            "pre_clear",
            "review",
        ), f"Expected pre_clear or review for clean entity, got {result.triage_tier}"


# ---------------------------------------------------------------------------
# 2. Sanctions hit → high entity risk + high fraud risk
# ---------------------------------------------------------------------------


class TestSanctionsHit:
    """Sanctions hit → high risk in entity layer + fraud layer; named signals."""

    def test_sanctions_hit_elevates_entity_score(self):
        run_id = str(uuid.uuid4())
        ev_hit = _ev(run_id, "sanctions", 1, "sanctions_hit", "ACME CORP")
        ev_flag = _ev(run_id, "sanctions", 1, "sanctions_risk_flag", "true")

        sigs = entity_legitimacy_signals([ev_hit, ev_flag])
        signal_names = {s.name for s in sigs}
        assert (
            "sanctions_hit" in signal_names
        ), "Expected sanctions_hit signal when sanctions evidence present"
        elevated = [s for s in sigs if s.direction == "elevated"]
        assert len(elevated) >= 1

    def test_sanctions_hit_elevates_overall_score(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "sanctions", 1, "sanctions_hit", "BADCO"),
            _ev(run_id, "sanctions", 1, "sanctions_risk_flag", "true"),
            _ev(run_id, "whois", 2, "domain_age_days", "100", normalized="100"),
        ]
        result = ScoringEngine().score(evidence, [])
        assert (
            result.overall_score > 60
        ), f"Expected high risk (> 60) for sanctions hit, got {result.overall_score}"

    def test_sanctions_hit_triage_escalate(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "sanctions", 1, "sanctions_hit", "BADCO"),
            _ev(run_id, "sanctions", 1, "sanctions_risk_flag", "true"),
            _ev(run_id, "whois", 2, "recently_registered", "true", normalized="true"),
            _ev(run_id, "dns", 2, "no_mx", "true", normalized="true"),
        ]
        result = ScoringEngine().score(evidence, [])
        assert (
            result.triage_tier == "escalate"
        ), f"Expected escalate for sanctions hit, got {result.triage_tier}"

    def test_sanctions_hit_fraud_flag_in_risk_layer(self):
        run_id = str(uuid.uuid4())
        ev_hit = _ev(run_id, "sanctions", 1, "sanctions_hit", "BADCO")
        ev_flag = _ev(run_id, "sanctions", 1, "sanctions_risk_flag", "true")

        sigs = fraud_staging_risk_signals([ev_hit, ev_flag])
        signal_names = {s.name for s in sigs}
        assert "sanctions_hit_fraud_flag" in signal_names

    def test_sanctions_cleared_is_trust_signal(self):
        run_id = str(uuid.uuid4())
        ev = _ev(run_id, "sanctions", 1, "sanctions_screened", "no_hit")

        sigs = entity_legitimacy_signals([ev])
        trust = [s for s in sigs if s.name == "sanctions_cleared"]
        assert len(trust) == 1
        assert trust[0].direction == "trust"
        assert ev.id in trust[0].evidence_ids


# ---------------------------------------------------------------------------
# 3. Registry mismatch → elevated representation risk
# ---------------------------------------------------------------------------


class TestRegistryMismatch:
    """Registry mismatch → elevated representation layer."""

    def test_name_mismatch_elevates_representation_score(self):
        run_id = str(uuid.uuid4())
        ev_name = _ev(run_id, "opencorporates", 1, "company_name", "Other Corp")

        fc = _fc(
            run_id,
            "company_name",
            "Test Corp",
            "Other Corp",
            "mismatch",
            ev_name.id,
        )
        sigs = representation_confidence_signals([ev_name], [fc])
        mismatch = [s for s in sigs if s.direction == "elevated"]
        assert len(mismatch) >= 1
        signal_names = {s.name for s in mismatch}
        assert "company_name_mismatch" in signal_names

    def test_registry_mismatch_high_overall_score(self):
        run_id = str(uuid.uuid4())
        ev_name = _ev(run_id, "opencorporates", 1, "company_name", "Other Corp")
        ev_recent = _ev(
            run_id, "whois", 2, "recently_registered", "true", normalized="true"
        )
        ev_no_mx = _ev(run_id, "dns", 2, "no_mx", "true", normalized="true")

        fc = _fc(
            run_id,
            "company_name",
            "Test Corp",
            "Other Corp",
            "mismatch",
            ev_name.id,
        )
        result = ScoringEngine().score([ev_name, ev_recent, ev_no_mx], [fc])
        assert result.overall_score > 55, (
            f"Expected elevated risk for registry mismatch + fresh domain, "
            f"got {result.overall_score}"
        )

    def test_mismatch_signal_cites_evidence(self):
        run_id = str(uuid.uuid4())
        ev_name = _ev(run_id, "opencorporates", 1, "company_name", "Other Corp")
        fc = _fc(
            run_id, "company_name", "Test Corp", "Other Corp", "mismatch", ev_name.id
        )
        sigs = representation_confidence_signals([ev_name], [fc])
        for sig in sigs:
            if sig.name == "company_name_mismatch":
                assert ev_name.id in sig.evidence_ids


# ---------------------------------------------------------------------------
# 4. Thin web + datacenter IP + fresh domain → stacking fraud layer
# ---------------------------------------------------------------------------


class TestFraudStagingStack:
    """Thin website + datacenter IP + recently registered domain stacks risk."""

    def test_fraud_layer_score_elevated_with_stack(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "whois", 2, "recently_registered", "true", normalized="true"),
            _ev(run_id, "dns", 2, "no_mx", "true", normalized="true"),
            _ev(run_id, "ipinfo", 2, "ip_anonymized_network", "true"),
            _ev(run_id, "ipinfo", 2, "ip_hosting", "true"),
            _ev(run_id, "web", 3, "web_thin_footprint", "true"),
        ]
        sigs = fraud_staging_risk_signals(evidence)
        signal_names = {s.name for s in sigs}
        assert "recently_registered_no_mx" in signal_names
        assert "datacenter_or_anonymized_ip" in signal_names
        assert "thin_website" in signal_names
        elevated = [s for s in sigs if s.direction == "elevated"]
        assert (
            len(elevated) >= 3
        ), f"Expected ≥3 elevated signals for full fraud stack, got {len(elevated)}"

    def test_fraud_stack_escalates_triage(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "whois", 2, "recently_registered", "true", normalized="true"),
            _ev(run_id, "dns", 2, "no_mx", "true", normalized="true"),
            _ev(run_id, "ipinfo", 2, "ip_anonymized_network", "true"),
            _ev(run_id, "ipinfo", 2, "ip_hosting", "true"),
            _ev(run_id, "web", 3, "web_thin_footprint", "true"),
        ]
        result = ScoringEngine().score(evidence, [])
        assert (
            result.triage_tier == "escalate"
        ), f"Expected escalate for full fraud stack, got {result.triage_tier}"

    def test_each_fraud_signal_cites_evidence(self):
        run_id = str(uuid.uuid4())
        ev_recent = _ev(
            run_id, "whois", 2, "recently_registered", "true", normalized="true"
        )
        ev_no_mx = _ev(run_id, "dns", 2, "no_mx", "true", normalized="true")
        ev_anon = _ev(run_id, "ipinfo", 2, "ip_anonymized_network", "true")
        ev_thin = _ev(run_id, "web", 3, "web_thin_footprint", "true")

        evidence = [ev_recent, ev_no_mx, ev_anon, ev_thin]
        ev_id_set = {e.id for e in evidence}
        sigs = fraud_staging_risk_signals(evidence)

        for sig in sigs:
            if sig.name in (
                "recently_registered_no_mx",
                "datacenter_or_anonymized_ip",
                "thin_website",
            ):
                assert (
                    len(sig.evidence_ids) >= 1
                ), f"Signal {sig.name!r} must cite ≥1 evidence row"
                for ev_id in sig.evidence_ids:
                    assert (
                        ev_id in ev_id_set
                    ), f"Signal {sig.name!r} references unknown evidence_id {ev_id!r}"

    def test_active_employee_footprint_is_trust(self):
        run_id = str(uuid.uuid4())
        ev_footprint = _ev(run_id, "web", 3, "web_employee_footprint", "8")

        sigs = fraud_staging_risk_signals([ev_footprint])
        footprint_sigs = [s for s in sigs if s.name == "active_employee_footprint"]
        assert len(footprint_sigs) == 1
        assert footprint_sigs[0].direction == "trust"
        assert ev_footprint.id in footprint_sigs[0].evidence_ids


# ---------------------------------------------------------------------------
# 5. Reduced source coverage → lower confidence not defaulted-low
# ---------------------------------------------------------------------------


class TestSourceCoverageConfidence:
    """Reduced source coverage → lower confidence, NOT defaulted to low risk."""

    def test_no_evidence_is_zero_confidence(self):
        result = ScoringEngine().score([], [])
        assert result.confidence == 0.0

    def test_no_evidence_not_low_risk(self):
        result = ScoringEngine().score([], [])
        assert (
            result.overall_score > 30
        ), "No evidence must NOT default to low risk (pre-clear)"

    def test_only_tier2_gives_partial_confidence(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "dns", 2, "mx_present", "true", normalized="true"),
        ]
        result = ScoringEngine().score(evidence, [])
        # Only Tier-2 → 1/3 tiers = 0.33 confidence
        assert result.confidence < 1.0, "Partial coverage must not give 1.0 confidence"
        assert (
            result.entity_score > 0
        ), "Missing Tier-1 must not produce perfect entity score"

    def test_all_three_tiers_gives_full_confidence(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "X Corp"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "web", 3, "web_brand", "X Corp"),
        ]
        result = ScoringEngine().score(evidence, [])
        assert (
            result.confidence == 1.0
        ), "All three tiers present → confidence should be 1.0"


# ---------------------------------------------------------------------------
# 6. INTEGRATION: every layer score traces to ≥1 evidence row
# ---------------------------------------------------------------------------


class TestSignalEvidenceIntegrity:
    """INTEGRATION: every signal references ≥1 valid evidence row (no orphans)."""

    def test_no_orphan_signals_for_full_stack(self, db: Session):
        _, run_id = _make_run(db)

        evidence_rows = [
            Evidence(
                verification_run_id=run_id,
                source="opencorporates",
                tier=1,
                field="company_name",
                raw_value="Test Corp",
                normalized_value="Test Corp",
                confidence=0.9,
                fetched_at=datetime.now(tz=timezone.utc),
            ),
            Evidence(
                verification_run_id=run_id,
                source="sanctions",
                tier=1,
                field="sanctions_screened",
                raw_value="no_hit",
                normalized_value="no_hit",
                confidence=1.0,
                fetched_at=datetime.now(tz=timezone.utc),
            ),
            Evidence(
                verification_run_id=run_id,
                source="whois",
                tier=2,
                field="domain_age_days",
                raw_value="2000",
                normalized_value="2000",
                confidence=0.9,
                fetched_at=datetime.now(tz=timezone.utc),
            ),
            Evidence(
                verification_run_id=run_id,
                source="dns",
                tier=2,
                field="mx_present",
                raw_value="true",
                normalized_value="true",
                confidence=0.9,
                fetched_at=datetime.now(tz=timezone.utc),
            ),
            Evidence(
                verification_run_id=run_id,
                source="web",
                tier=3,
                field="web_employee_footprint",
                raw_value="5",
                normalized_value="5",
                confidence=0.7,
                fetched_at=datetime.now(tz=timezone.utc),
            ),
        ]
        db.add_all(evidence_rows)
        db.commit()

        fc = FieldComparison(
            verification_run_id=run_id,
            field_name="company_name",
            submitted_value="Test Corp",
            discovered_value="Test Corp",
            match_status="match",
            evidence_id=evidence_rows[0].id,
        )
        db.add(fc)
        db.commit()

        stage = ScoringStage()
        stage.run(run_id, db, {})

        assessment = (
            db.query(RiskAssessment)
            .filter(RiskAssessment.verification_run_id == run_id)
            .first()
        )
        assert assessment is not None

        all_ev_ids = {
            e.id
            for e in db.query(Evidence)
            .filter(Evidence.verification_run_id == run_id)
            .all()
        }

        for sig_dict in assessment.contributing_signals or []:
            for ev_id in sig_dict.get("evidence_ids", []):
                assert ev_id in all_ev_ids, (
                    f"Signal {sig_dict['name']!r} references orphan "
                    f"evidence_id {ev_id!r}"
                )

    def test_each_layer_has_at_least_one_signal(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "web", 3, "web_brand", "Test Corp"),
        ]
        comparisons = [
            _fc(
                run_id,
                "company_name",
                "Test Corp",
                "Test Corp",
                "match",
                evidence[0].id,
            )
        ]

        result = ScoringEngine().score(evidence, comparisons)

        layers = {s.layer for s in result.contributing_signals}
        assert "entity" in layers, "Entity layer must have ≥1 signal"
        assert "infrastructure" in layers, "Infrastructure layer must have ≥1 signal"
        assert "representation" in layers, "Representation layer must have ≥1 signal"
        assert "risk" in layers, "Fraud/risk layer must have ≥1 signal"


# ---------------------------------------------------------------------------
# 7. Cross-submission IP/ASN reuse detection
# ---------------------------------------------------------------------------


class TestIPASNReuse:
    """Cross-submission IP/ASN reuse detection via DB query."""

    def test_asn_reuse_single_prior_run(self, db: Session):
        """One prior run sharing the same ASN → ip_asn_reuse signal."""
        _, run_id_1 = _make_run(db)
        _, run_id_2 = _make_run(db)

        # Prior run has ip_asn evidence
        prior_asn_ev = Evidence(
            verification_run_id=run_id_1,
            source="ipinfo",
            tier=2,
            field="ip_asn",
            raw_value="AS12345",
            normalized_value="AS12345",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        db.add(prior_asn_ev)
        db.commit()

        # Current run has same ASN
        current_asn_ev = Evidence(
            verification_run_id=run_id_2,
            source="ipinfo",
            tier=2,
            field="ip_asn",
            raw_value="AS12345",
            normalized_value="AS12345",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        db.add(current_asn_ev)
        db.commit()

        sigs = fraud_staging_risk_signals([current_asn_ev], run_id=run_id_2, db=db)
        signal_names = {s.name for s in sigs}
        assert (
            "ip_asn_reuse" in signal_names or "ip_asn_reuse_high" in signal_names
        ), "Expected ip_asn_reuse signal when same ASN appears in prior submissions"

    def test_asn_reuse_high_with_multiple_prior_runs(self, db: Session):
        """Three or more prior runs sharing the same ASN → ip_asn_reuse_high."""
        _, run_id_current = _make_run(db)
        prior_run_ids = []
        for _ in range(3):
            _, run_id = _make_run(db)
            prior_run_ids.append(run_id)

        # Create ASN evidence for each prior run
        for prior_run_id in prior_run_ids:
            prior_ev = Evidence(
                verification_run_id=prior_run_id,
                source="ipinfo",
                tier=2,
                field="ip_asn",
                raw_value="AS99999",
                normalized_value="AS99999",
                confidence=0.9,
                fetched_at=datetime.now(tz=timezone.utc),
            )
            db.add(prior_ev)
        db.commit()

        # Current run evidence
        current_asn_ev = Evidence(
            verification_run_id=run_id_current,
            source="ipinfo",
            tier=2,
            field="ip_asn",
            raw_value="AS99999",
            normalized_value="AS99999",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        db.add(current_asn_ev)
        db.commit()

        sigs = fraud_staging_risk_signals(
            [current_asn_ev], run_id=run_id_current, db=db
        )
        signal_names = {s.name for s in sigs}
        assert (
            "ip_asn_reuse_high" in signal_names
        ), "Expected ip_asn_reuse_high when ≥3 prior runs share same ASN"

    def test_no_asn_reuse_without_prior_runs(self, db: Session):
        """No prior runs with same ASN → no reuse signal."""
        _, run_id = _make_run(db)

        current_asn_ev = Evidence(
            verification_run_id=run_id,
            source="ipinfo",
            tier=2,
            field="ip_asn",
            raw_value="AS77777",
            normalized_value="AS77777",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        db.add(current_asn_ev)
        db.commit()

        sigs = fraud_staging_risk_signals([current_asn_ev], run_id=run_id, db=db)
        signal_names = {s.name for s in sigs}
        assert "ip_asn_reuse" not in signal_names
        assert "ip_asn_reuse_high" not in signal_names


# ---------------------------------------------------------------------------
# 8. IP country mismatch → elevated representation signal
# ---------------------------------------------------------------------------


class TestIPCountryMismatch:
    """IP country mismatch is detected and routed to the representation layer."""

    def test_ip_country_mismatch_elevated(self):
        run_id = str(uuid.uuid4())
        ev = _ev(run_id, "ipinfo", 2, "ip_country_mismatch", "true", normalized="true")
        sigs = representation_confidence_signals([ev], [])
        signal_names = {s.name for s in sigs}
        assert "ip_country_mismatch" in signal_names
        mismatch_sig = next((s for s in sigs if s.name == "ip_country_mismatch"), None)
        assert mismatch_sig is not None
        assert mismatch_sig.direction == "elevated"
        assert ev.id in mismatch_sig.evidence_ids

    def test_ip_country_match_trust(self):
        run_id = str(uuid.uuid4())
        ev = _ev(run_id, "ipinfo", 2, "ip_country_match", "true", normalized="true")
        sigs = representation_confidence_signals([ev], [])
        signal_names = {s.name for s in sigs}
        assert "ip_country_match" in signal_names
        match_sig = next((s for s in sigs if s.name == "ip_country_match"), None)
        assert match_sig is not None
        assert match_sig.direction == "trust"


# ---------------------------------------------------------------------------
# 9. Trust signals catalog completeness
# ---------------------------------------------------------------------------


class TestTrustSignals:
    """Trust signals: long-lived domain, stable web, active footprint."""

    def test_long_lived_domain_in_infrastructure_layer(self):
        run_id = str(uuid.uuid4())
        ev = _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000")
        sigs = infrastructure_legitimacy_signals([ev])
        signal_names = {s.name for s in sigs}
        assert "long_lived_domain" in signal_names
        sig = next(s for s in sigs if s.name == "long_lived_domain")
        assert sig.direction == "trust"
        assert ev.id in sig.evidence_ids

    def test_long_lived_domain_in_risk_layer(self):
        run_id = str(uuid.uuid4())
        ev = _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000")
        sigs = fraud_staging_risk_signals([ev])
        signal_names = {s.name for s in sigs}
        assert "long_lived_domain_trust" in signal_names
        sig = next(s for s in sigs if s.name == "long_lived_domain_trust")
        assert sig.direction == "trust"
        assert ev.id in sig.evidence_ids

    def test_stable_web_presence_trust(self):
        run_id = str(uuid.uuid4())
        ev_brand = _ev(run_id, "web", 3, "web_brand", "Test Corp")
        ev_footprint = _ev(run_id, "web", 3, "web_employee_footprint", "10")
        sigs = fraud_staging_risk_signals([ev_brand, ev_footprint])
        all_names = {s.name for s in sigs}
        has_presence = (
            "stable_web_presence" in all_names
            or "active_employee_footprint" in all_names
        )
        assert has_presence

    def test_registry_confirmation_full_stack(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Acme Inc"),
            _ev(run_id, "opencorporates", 1, "registration_number", "99999"),
            _ev(
                run_id,
                "opencorporates",
                1,
                "registration_status",
                "Active",
                normalized="active",
            ),
        ]
        sigs = entity_legitimacy_signals(evidence)
        trust_names = {s.name for s in sigs if s.direction == "trust"}
        assert "registry_name_confirmed" in trust_names
        assert "registration_number_present" in trust_names
        assert "registry_status_active" in trust_names


# ---------------------------------------------------------------------------
# P4-T4 — web contact trust only for company-domain emails
# ---------------------------------------------------------------------------


def _web_email(on_domain: bool) -> Evidence:
    ev = _ev("run-web", "web", 3, "web_contacts_email", "x@acme.com")
    ev.raw_payload = {"emails": ["x@acme.com"], "on_company_domain": on_domain}
    return ev


def test_web_contact_trust_requires_company_domain_email():
    from app.scoring.signals import representation_confidence_signals

    on = {s.name for s in representation_confidence_signals([_web_email(True)], [])}
    off = {s.name for s in representation_confidence_signals([_web_email(False)], [])}
    assert "web_contact_email_found" in on
    assert "web_contact_email_found" not in off
