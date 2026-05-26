"""Tests for the core data model (P0-T4).

Uses an in-memory SQLite database — no live Postgres required — so these
tests pass in CI without any external services.

Coverage:
  - Schema creation (all tables build without error)
  - Round-trip create / read for Submission, VerificationRun, Evidence
  - VerificationRun.supersedes self-reference chain
  - AuditEvent append-only pattern (no .update() / .delete() helpers exposed)
"""

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

# Import all models so Base.metadata is fully populated.
import app.models  # noqa: F401 — side-effect registers all models
from app.db.session import Base
from app.models.audit_event import AuditEvent
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.operator import Operator
from app.models.submission import Submission
from app.models.verification_run import VerificationRun


@pytest.fixture(scope="module")
def engine():
    """Ephemeral in-memory SQLite engine with all tables created."""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    """Transactional session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Schema existence checks
# ---------------------------------------------------------------------------


def test_all_tables_created(engine):
    """All 10 entity tables should exist after create_all."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "entity",
        "submission",
        "verification_run",
        "evidence",
        "field_comparison",
        "risk_assessment",
        "risk_assessment_evidence",
        "report",
        "operator",
        "review",
        "audit_event",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


# ---------------------------------------------------------------------------
# Round-trip: Submission -> VerificationRun -> Evidence chain
# ---------------------------------------------------------------------------


def test_submission_create_and_read(session):
    """Create a Submission and retrieve it."""
    entity = Entity(canonical_name="Acme Corp", canonical_domain="acme.example")
    session.add(entity)
    session.flush()

    sub = Submission(
        company_name="Acme Corp",
        domain="acme.example",
        work_email="cto@acme.example",
        country="US",
        entity_id=entity.id,
        source_ip="203.0.113.1",
        user_agent="TestAgent/1.0",
    )
    session.add(sub)
    session.flush()

    retrieved = session.get(Submission, sub.id)
    assert retrieved is not None
    assert retrieved.company_name == "Acme Corp"
    assert retrieved.domain == "acme.example"
    assert retrieved.country == "US"
    assert retrieved.source_ip == "203.0.113.1"
    assert retrieved.entity_id == entity.id


def test_verification_run_create_and_read(session):
    """Create a VerificationRun linked to a Submission and Entity."""
    entity = Entity(canonical_name="Beta Ltd")
    session.add(entity)
    session.flush()

    sub = Submission(
        company_name="Beta Ltd",
        domain="beta.example",
        work_email="ops@beta.example",
        country="GB",
        entity_id=entity.id,
    )
    session.add(sub)
    session.flush()

    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="pending",
    )
    session.add(run)
    session.flush()

    retrieved = session.get(VerificationRun, run.id)
    assert retrieved is not None
    assert retrieved.status == "pending"
    assert retrieved.submission_id == sub.id
    assert retrieved.entity_id == entity.id
    assert retrieved.supersedes_id is None


def test_evidence_linked_to_verification_run(session):
    """Create Evidence rows attached to a VerificationRun."""
    entity = Entity(canonical_name="Gamma Inc")
    session.add(entity)
    session.flush()

    sub = Submission(
        company_name="Gamma Inc",
        domain="gamma.example",
        work_email="info@gamma.example",
        country="DE",
        entity_id=entity.id,
    )
    session.add(sub)
    session.flush()

    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="running",
    )
    session.add(run)
    session.flush()

    ev = Evidence(
        verification_run_id=run.id,
        source="opencorporates",
        tier=1,
        field="company_name",
        raw_value="Gamma Incorporated",
        normalized_value="Gamma Inc",
        confidence=0.92,
    )
    session.add(ev)
    session.flush()

    retrieved = session.get(Evidence, ev.id)
    assert retrieved is not None
    assert retrieved.source == "opencorporates"
    assert retrieved.tier == 1
    assert retrieved.confidence == pytest.approx(0.92)
    assert retrieved.verification_run_id == run.id


# ---------------------------------------------------------------------------
# VerificationRun.supersedes self-reference
# ---------------------------------------------------------------------------


def test_verification_run_supersedes_self_reference(session):
    """A new run can supersede a prior run via supersedes_id (self-FK)."""
    entity = Entity(canonical_name="Delta Co")
    session.add(entity)
    session.flush()

    sub = Submission(
        company_name="Delta Co",
        domain="delta.example",
        work_email="ceo@delta.example",
        country="CA",
        entity_id=entity.id,
    )
    session.add(sub)
    session.flush()

    first_run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="complete",
    )
    session.add(first_run)
    session.flush()

    second_run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="running",
        supersedes_id=first_run.id,
    )
    session.add(second_run)
    session.flush()

    retrieved = session.get(VerificationRun, second_run.id)
    assert retrieved is not None
    assert retrieved.supersedes_id == first_run.id

    # Navigate the ORM relationship.
    assert retrieved.superseded_run is not None
    assert retrieved.superseded_run.id == first_run.id
    assert retrieved.superseded_run.status == "complete"


# ---------------------------------------------------------------------------
# AuditEvent — append-only: verify no update/delete methods on the class
# ---------------------------------------------------------------------------


def test_audit_event_has_no_update_or_delete_methods():
    """AuditEvent must not expose .update() or .delete() instance methods.

    The append-only contract (ARCHITECTURE § 6) is enforced by keeping
    these helpers off the model class.  Database-level enforcement is a
    deployment concern (P3-T3); this test guards the model API.
    """
    assert not hasattr(
        AuditEvent, "update"
    ), "AuditEvent must not expose an .update() method (append-only table)"
    assert not hasattr(
        AuditEvent, "delete"
    ), "AuditEvent must not expose a .delete() method (append-only table)"


def test_audit_event_create(session):
    """AuditEvent rows can be inserted (append)."""
    operator = Operator(
        email="admin@entityiq.example",
        full_name="Admin User",
        role="lead",
    )
    session.add(operator)
    session.flush()

    evt = AuditEvent(
        event_type="operator.sign_in",
        operator_id=operator.id,
        description="Operator signed in via session auth",
    )
    session.add(evt)
    session.flush()

    retrieved = session.get(AuditEvent, evt.id)
    assert retrieved is not None
    assert retrieved.event_type == "operator.sign_in"
    assert retrieved.operator_id == operator.id
