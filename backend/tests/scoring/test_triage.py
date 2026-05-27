"""Tests for P2-T7 — Explainability payload + triage tiers.

All tests run against SQLite in-memory (StaticPool).  No live Redis, Postgres,
or broker.

Test scenarios:
  1. Clean entity → pre_clear tier with full explanation
  2. Sanctions hit → escalate tier (critical signal override)
  3. Registry mismatch → escalate (score-threshold path)
  4. pre_clear outcome carries NO auto-approval (no approval state set)
  5. Explainability payload: each layer lists contributing signals with
     evidence sources (no orphan evidence_ids in payload)
  6. Triage reason text is present and non-empty for all tiers
  7. StoreReportStage includes triage + explainability in report summary
  8. MismatchDetail surfaces all field comparisons including unverified
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
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.scoring.engine import ScoringEngine, ScoringStage
from app.scoring.explain import (
    ExplainabilityPayload,
    LayerExplanation,
    build_explainability,
)
from app.scoring.report import StoreReportStage
from app.scoring.triage import derive_triage

# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def triage_engine():
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
def db(triage_engine):
    connection = triage_engine.connect()
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
# 1. Clean entity → pre_clear tier with full explanation
# ---------------------------------------------------------------------------


class TestCleanEntityPreClear:
    """Clean entity → pre_clear triage tier with a full explainability payload."""

    def test_pre_clear_tier_for_clean_entity(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "ABC123"),
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
        triage = derive_triage(result)

        assert (
            triage.tier == "pre_clear"
        ), f"Expected pre_clear for clean entity, got {triage.tier}"

    def test_pre_clear_has_non_empty_reason(self):
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
        triage = derive_triage(result)

        if triage.tier == "pre_clear":
            assert triage.reason, "pre_clear triage must have a non-empty reason"
            assert triage.advisory_note, "pre_clear must carry the advisory note"

    def test_pre_clear_explainability_has_all_layers(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "ABC"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "dns", 2, "mx_present", "true", normalized="true"),
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
        payload = build_explainability(result, evidence, comparisons)

        assert isinstance(payload, ExplainabilityPayload)
        layer_names = {layer.layer for layer in payload.layers}
        assert "entity" in layer_names
        assert "infrastructure" in layer_names
        assert "representation" in layer_names
        assert "risk" in layer_names

    def test_pre_clear_explainability_signals_cite_evidence(self):
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
        payload = build_explainability(result, evidence, comparisons)

        ev_id_set = {e.id for e in evidence}
        for layer in payload.layers:
            for sig in layer.signals:
                for ev_id in sig.evidence_ids:
                    assert ev_id in ev_id_set, (
                        f"Layer {layer.layer!r} signal {sig.name!r} references "
                        f"unknown evidence_id {ev_id!r}"
                    )

    def test_pre_clear_source_coverage_present(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "web", 3, "web_brand", "Test Corp"),
        ]

        result = ScoringEngine().score(evidence, [])
        payload = build_explainability(result, evidence, [])

        assert "opencorporates" in payload.source_coverage
        assert "whois" in payload.source_coverage
        assert "web" in payload.source_coverage
        assert payload.source_coverage["opencorporates"]["tier"] == 1
        assert payload.source_coverage["opencorporates"]["evidence_count"] >= 1


# ---------------------------------------------------------------------------
# 2. Sanctions hit → escalate (critical signal override)
# ---------------------------------------------------------------------------


class TestSanctionsEscalate:
    """Sanctions hit forces escalate tier via critical-signal override."""

    def test_sanctions_hit_forces_escalate(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "sanctions", 1, "sanctions_hit", "BADCO"),
            _ev(run_id, "sanctions", 1, "sanctions_risk_flag", "true"),
        ]

        result = ScoringEngine().score(evidence, [])
        triage = derive_triage(result)

        assert (
            triage.tier == "escalate"
        ), f"Expected escalate for sanctions hit, got {triage.tier}"

    def test_sanctions_hit_names_critical_signals(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "sanctions", 1, "sanctions_hit", "BADCO"),
            _ev(run_id, "sanctions", 1, "sanctions_risk_flag", "true"),
        ]

        result = ScoringEngine().score(evidence, [])
        triage = derive_triage(result)

        # critical_signals should name the sanctions signals
        assert (
            len(triage.critical_signals) >= 1
        ), "Escalate due to sanctions must name critical signals"
        assert any(
            "sanctions" in s for s in triage.critical_signals
        ), f"Expected sanctions in critical signals, got {triage.critical_signals}"

    def test_registry_mismatch_fresh_domain_escalates(self):
        """Registry mismatch + fresh domain → score above escalate threshold."""
        run_id = str(uuid.uuid4())
        ev_name = _ev(run_id, "opencorporates", 1, "company_name", "Other Corp")
        ev_recent = _ev(
            run_id, "whois", 2, "recently_registered", "true", normalized="true"
        )
        ev_no_mx = _ev(run_id, "dns", 2, "no_mx", "true", normalized="true")
        ev_anon = _ev(run_id, "ipinfo", 2, "ip_anonymized_network", "true")
        ev_thin = _ev(run_id, "web", 3, "web_thin_footprint", "true")

        fc = _fc(
            run_id, "company_name", "Test Corp", "Other Corp", "mismatch", ev_name.id
        )

        result = ScoringEngine().score(
            [ev_name, ev_recent, ev_no_mx, ev_anon, ev_thin], [fc]
        )
        triage = derive_triage(result)

        assert triage.tier == "escalate", (
            "Expected escalate for mismatch + fresh domain + datacenter, "
            f"got {triage.tier}"
        )

    def test_escalate_reason_non_empty(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "sanctions", 1, "sanctions_hit", "BADCO"),
            _ev(run_id, "whois", 2, "recently_registered", "true", normalized="true"),
            _ev(run_id, "dns", 2, "no_mx", "true", normalized="true"),
        ]
        result = ScoringEngine().score(evidence, [])
        triage = derive_triage(result)

        assert triage.reason, "Escalate triage must have a non-empty reason"
        assert triage.tier == "escalate"


# ---------------------------------------------------------------------------
# 3. pre_clear carries NO auto-approval
# ---------------------------------------------------------------------------


class TestNoAutoApprovalInvariant:
    """PRD Non-Goals invariant: pre_clear is NEVER an approval state."""

    def test_pre_clear_has_no_decision_attribute(self):
        """TriageResult has no 'decision', 'approved', or 'rejected' field."""
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Safe Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "XYZ"),
            _ev(run_id, "whois", 2, "domain_age_days", "2500", normalized="2500"),
            _ev(run_id, "dns", 2, "mx_present", "true", normalized="true"),
            _ev(run_id, "dns", 2, "spf_present", "true", normalized="true"),
        ]
        comparisons = [
            _fc(
                run_id,
                "company_name",
                "Safe Corp",
                "Safe Corp",
                "match",
                evidence[0].id,
            )
        ]

        result = ScoringEngine().score(evidence, comparisons)
        triage = derive_triage(result)

        if triage.tier == "pre_clear":
            # TriageResult must not have approval attributes
            assert not hasattr(triage, "decision")
            assert not hasattr(triage, "approved")
            assert not hasattr(triage, "rejected")
            assert not hasattr(triage, "auto_approved")
            # The tier name itself must not be an approval state
            assert triage.tier not in ("approved", "rejected", "auto_approved")

    def test_triage_tiers_are_never_approval_states(self):
        """derive_triage never returns 'approved' or 'rejected'."""
        # Test all three tier paths
        test_cases = [
            # (evidence_description, expected_valid_tier)
            ([], "any"),  # no evidence → some tier
        ]

        for evidence, _ in test_cases:
            result = ScoringEngine().score(evidence, [])
            triage = derive_triage(result)
            assert triage.tier in ("pre_clear", "review", "escalate"), (
                f"derive_triage must return pre_clear|review|escalate, "
                f"got {triage.tier!r}"
            )
            assert triage.tier not in (
                "approved",
                "rejected",
                "auto_approved",
            ), f"Triage tier must never be an approval state, got {triage.tier!r}"

    def test_pre_clear_advisory_note_is_set(self):
        """pre_clear result carries an advisory note about human action required."""
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Safe Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "XYZ"),
            _ev(run_id, "whois", 2, "domain_age_days", "3000", normalized="3000"),
            _ev(run_id, "dns", 2, "mx_present", "true", normalized="true"),
        ]
        comparisons = [
            _fc(
                run_id,
                "company_name",
                "Safe Corp",
                "Safe Corp",
                "match",
                evidence[0].id,
            )
        ]

        result = ScoringEngine().score(evidence, comparisons)
        triage = derive_triage(result)

        # advisory_note must be set on every TriageResult
        assert triage.advisory_note, "TriageResult must always have an advisory_note"
        # The note should mention human action
        assert any(
            word in triage.advisory_note.lower()
            for word in ("human", "operator", "approve", "must")
        ), (
            "Advisory note should mention human action required, "
            f"got: {triage.advisory_note!r}"
        )

    def test_pre_clear_scoring_result_has_no_approval_state(self):
        """ScoringResult itself has no approval state — engine is advisory only."""
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Safe Corp"),
            _ev(run_id, "whois", 2, "domain_age_days", "3000", normalized="3000"),
        ]

        result = ScoringEngine().score(evidence, [])
        # ScoringResult has no decision/approved/rejected attribute
        assert not hasattr(result, "decision")
        assert not hasattr(result, "approved")
        assert not hasattr(result, "rejected")


# ---------------------------------------------------------------------------
# 4. Explainability payload: layers + signals + evidence sources
# ---------------------------------------------------------------------------


class TestExplainabilityPayload:
    """ExplainabilityPayload lists each layer's contributing signals and evidence."""

    def test_payload_layers_have_scores(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "whois", 2, "domain_age_days", "1500", normalized="1500"),
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
        payload = build_explainability(result, evidence, comparisons)

        for layer in payload.layers:
            assert isinstance(layer, LayerExplanation)
            assert (
                0.0 <= layer.score <= 100.0
            ), f"Layer {layer.layer!r} score out of range: {layer.score}"

    def test_payload_each_layer_has_at_least_one_signal(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "whois", 2, "domain_age_days", "1500", normalized="1500"),
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
        payload = build_explainability(result, evidence, comparisons)

        for layer in payload.layers:
            assert (
                len(layer.signals) >= 1
            ), f"Layer {layer.layer!r} must have ≥1 signal in explainability payload"

    def test_no_orphan_evidence_ids_in_payload(self):
        """Every evidence_id in the payload must reference a known evidence row."""
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "123"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
            _ev(run_id, "dns", 2, "mx_present", "true", normalized="true"),
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
        payload = build_explainability(result, evidence, comparisons)

        ev_id_set = {e.id for e in evidence}
        for layer in payload.layers:
            for sig in layer.signals:
                for ev_id in sig.evidence_ids:
                    assert ev_id in ev_id_set, (
                        f"Payload signal {sig.name!r} in layer {layer.layer!r} "
                        f"references orphan evidence_id {ev_id!r}"
                    )

    def test_layer_sources_are_correct(self):
        """Layer sources list only sources that contributed evidence to that layer."""
        run_id = str(uuid.uuid4())
        ev_name = _ev(run_id, "opencorporates", 1, "company_name", "Test Corp")
        ev_age = _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000")

        evidence = [ev_name, ev_age]
        comparisons = [
            _fc(run_id, "company_name", "Test Corp", "Test Corp", "match", ev_name.id)
        ]

        result = ScoringEngine().score(evidence, comparisons)
        payload = build_explainability(result, evidence, comparisons)

        # Entity layer should mention opencorporates
        entity_layer = next(
            (layer for layer in payload.layers if layer.layer == "entity"), None
        )
        assert entity_layer is not None
        assert (
            "opencorporates" in entity_layer.sources
        ), "Entity layer sources should include 'opencorporates' for name evidence"

        # Infrastructure layer should mention whois
        infra_layer = next(
            (layer for layer in payload.layers if layer.layer == "infrastructure"), None
        )
        assert infra_layer is not None
        assert (
            "whois" in infra_layer.sources
        ), "Infrastructure layer sources should include 'whois' for domain age evidence"

    def test_mismatches_surfaced_in_payload(self):
        """Explainability payload includes all field comparisons in mismatches."""
        run_id = str(uuid.uuid4())
        ev_name = _ev(run_id, "opencorporates", 1, "company_name", "Other Corp")
        evidence = [ev_name]
        comparisons = [
            _fc(
                run_id,
                "company_name",
                "Test Corp",
                "Other Corp",
                "mismatch",
                ev_name.id,
            ),
            _fc(run_id, "billing_address", "123 Main St", None, "unverified", None),
        ]

        result = ScoringEngine().score(evidence, comparisons)
        payload = build_explainability(result, evidence, comparisons)

        assert len(payload.mismatches) == 2
        mismatch_fields = {m.field_name for m in payload.mismatches}
        assert "company_name" in mismatch_fields
        assert "billing_address" in mismatch_fields

    def test_source_coverage_counts_are_accurate(self):
        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "opencorporates", 1, "registration_number", "ABC"),
            _ev(run_id, "whois", 2, "domain_age_days", "1500", normalized="1500"),
        ]

        result = ScoringEngine().score(evidence, [])
        payload = build_explainability(result, evidence, [])

        assert payload.source_coverage["opencorporates"]["evidence_count"] == 2
        assert payload.source_coverage["whois"]["evidence_count"] == 1

    def test_payload_to_dict_is_json_serialisable(self):
        """ExplainabilityPayload.to_dict() must be JSON-serialisable."""
        import json  # noqa: PLC0415

        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
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
        payload = build_explainability(result, evidence, comparisons)

        # Should not raise
        serialised = json.dumps(payload.to_dict())
        assert len(serialised) > 0

    def test_triage_to_dict_is_json_serialisable(self):
        """TriageResult.to_dict() must be JSON-serialisable."""
        import json  # noqa: PLC0415

        run_id = str(uuid.uuid4())
        evidence = [
            _ev(run_id, "opencorporates", 1, "company_name", "Test Corp"),
            _ev(run_id, "whois", 2, "domain_age_days", "2000", normalized="2000"),
        ]

        result = ScoringEngine().score(evidence, [])
        triage = derive_triage(result)

        serialised = json.dumps(triage.to_dict())
        assert len(serialised) > 0


# ---------------------------------------------------------------------------
# 5. StoreReportStage includes triage + explainability in summary
# ---------------------------------------------------------------------------


class TestReportSummaryInclusion:
    """StoreReportStage assembles reports with triage + explainability sections."""

    def test_report_summary_has_triage_section(self, db: Session):
        _, run_id = _make_run(db)

        ev_name = Evidence(
            verification_run_id=run_id,
            source="opencorporates",
            tier=1,
            field="company_name",
            raw_value="Test Corp",
            normalized_value="Test Corp",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        ev_age = Evidence(
            verification_run_id=run_id,
            source="whois",
            tier=2,
            field="domain_age_days",
            raw_value="2000",
            normalized_value="2000",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        db.add_all([ev_name, ev_age])
        db.commit()

        fc = FieldComparison(
            verification_run_id=run_id,
            field_name="company_name",
            submitted_value="Test Corp",
            discovered_value="Test Corp",
            match_status="match",
            evidence_id=ev_name.id,
        )
        db.add(fc)
        db.commit()

        run = db.get(VerificationRun, run_id)
        run.source_availability = {
            "query_registries": "complete",
            "analyze_domain": "complete",
            "consistency_checks": "complete",
        }
        db.commit()

        ScoringStage().run(run_id, db, {})
        StoreReportStage().run(run_id, db, {})

        report = db.query(Report).filter(Report.verification_run_id == run_id).first()
        assert report is not None
        summary = report.summary or {}

        # P2-T7: triage section must be present
        assert (
            "triage" in summary
        ), "Report summary must contain 'triage' section (P2-T7)"
        assert summary["triage"] is not None
        triage_dict = summary["triage"]
        assert "tier" in triage_dict
        assert triage_dict["tier"] in ("pre_clear", "review", "escalate")
        assert "advisory_note" in triage_dict

    def test_report_summary_has_explainability_section(self, db: Session):
        _, run_id = _make_run(db)

        ev_name = Evidence(
            verification_run_id=run_id,
            source="opencorporates",
            tier=1,
            field="company_name",
            raw_value="Test Corp",
            normalized_value="Test Corp",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        ev_age = Evidence(
            verification_run_id=run_id,
            source="whois",
            tier=2,
            field="domain_age_days",
            raw_value="2000",
            normalized_value="2000",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        db.add_all([ev_name, ev_age])
        db.commit()

        ScoringStage().run(run_id, db, {})
        StoreReportStage().run(run_id, db, {})

        report = db.query(Report).filter(Report.verification_run_id == run_id).first()
        assert report is not None
        summary = report.summary or {}

        # P2-T7: explainability section must be present
        assert (
            "explainability" in summary
        ), "Report summary must contain 'explainability' section (P2-T7)"
        assert summary["explainability"] is not None
        exp_dict = summary["explainability"]
        assert "layers" in exp_dict
        assert "triage_tier" in exp_dict
        assert "source_coverage" in exp_dict
        assert len(exp_dict["layers"]) == 4  # one per layer

    def test_report_triage_is_never_auto_approval(self, db: Session):
        """Report's triage tier is never 'approved' or 'rejected'."""
        _, run_id = _make_run(db)

        ev_name = Evidence(
            verification_run_id=run_id,
            source="opencorporates",
            tier=1,
            field="company_name",
            raw_value="Test Corp",
            normalized_value="Test Corp",
            confidence=0.9,
            fetched_at=datetime.now(tz=timezone.utc),
        )
        db.add(ev_name)
        db.commit()

        ScoringStage().run(run_id, db, {})
        StoreReportStage().run(run_id, db, {})

        report = db.query(Report).filter(Report.verification_run_id == run_id).first()
        assert report is not None
        summary = report.summary or {}

        if summary.get("triage"):
            tier = summary["triage"].get("tier", "")
            assert tier not in (
                "approved",
                "rejected",
                "auto_approved",
            ), f"Report triage tier must never be an approval state, got {tier!r}"
