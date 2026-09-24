"""P5-T3 — the demo dataset runs the real pipeline and spans every triage tier.

Each scenario declares the tier it is meant to illustrate; this test holds the
dataset to that, so a scoring change that silently re-tiers the demo is caught.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base
from app.demo_data import SCENARIOS, load_demo_data
from app.models.report import Report
from app.models.risk_assessment import RiskAssessment
from app.models.submission import Submission
from app.models.verification_run import VerificationRun


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def _tier_by_company(db: Session) -> dict[str, str]:
    rows = (
        db.query(Submission.company_name, RiskAssessment.triage_tier)
        .join(VerificationRun, VerificationRun.submission_id == Submission.id)
        .join(RiskAssessment, RiskAssessment.verification_run_id == VerificationRun.id)
        .all()
    )
    return dict(rows)


def test_every_scenario_lands_in_its_intended_tier(db):
    load_demo_data(db)
    tiers = _tier_by_company(db)
    for scenario in SCENARIOS:
        assert tiers[scenario.company_name] == scenario.expected_tier, (
            scenario.company_name,
            tiers[scenario.company_name],
        )


def test_dataset_covers_all_three_tiers():
    assert {s.expected_tier for s in SCENARIOS} == {"pre_clear", "review", "escalate"}


def test_every_run_completes_with_a_report(db):
    load_demo_data(db)
    runs = db.query(VerificationRun).all()
    assert len(runs) == len(SCENARIOS)
    assert all(r.status == "complete" for r in runs)
    assert db.query(Report).count() == len(SCENARIOS)


def test_sanctions_scenario_is_flagged(db):
    load_demo_data(db)
    ra = (
        db.query(RiskAssessment)
        .join(VerificationRun)
        .join(Submission)
        .filter(Submission.company_name.like("%Volga%"))
        .one()
    )
    names = {s["name"] for s in ra.contributing_signals}
    assert any("sanction" in n for n in names if "cleared" not in n), names


def test_load_is_idempotent(db):
    assert load_demo_data(db) == len(SCENARIOS)
    assert load_demo_data(db) == 0
    assert db.query(Submission).count() == len(SCENARIOS)


def test_each_demo_run_has_an_audit_trail(db):
    from app.models.audit_event import AuditEvent

    load_demo_data(db)
    for run in db.query(VerificationRun).all():
        events = (
            db.query(AuditEvent).filter(AuditEvent.verification_run_id == run.id).all()
        )
        assert any(e.event_type.endswith("submission_received") for e in events)


def test_identity_corroboration_states_are_represented(db):
    """Ticket 0053: the demo dataset must show every identity state, not just
    the "not available" state every scenario had before scenario-specific
    tax-ID / LinkedIn records were added.
    """
    from app.models.evidence import Evidence

    load_demo_data(db)
    evidence = db.query(Evidence).all()

    tax_id_status = {e.normalized_value for e in evidence if e.field == "tax_id_status"}
    assert "verified" in tax_id_status, "expected at least one verified FEIN"
    assert "not_found" in tax_id_status, "expected at least one unknown FEIN"

    name_match = {
        e.normalized_value for e in evidence if e.field == "tax_id_name_match"
    }
    assert "match" in name_match, "expected a verified FEIN with a name match"

    linkedin_presence = {
        e.normalized_value for e in evidence if e.field == "linkedin_presence"
    }
    assert "found" in linkedin_presence, "expected an established LinkedIn page"
    assert (
        "not_found" in linkedin_presence
    ), "expected a submitted LinkedIn URL that doesn't resolve"

    linkedin_website = [e for e in evidence if e.field == "linkedin_website"]
    assert any(
        e.raw_value for e in linkedin_website
    ), "expected the established LinkedIn page to list a matching website"
