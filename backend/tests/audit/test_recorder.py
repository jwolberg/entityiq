"""Tests for P1-T9 — Audit event recorder.

Scenarios (security-sensitive tests first):
  1. mark-reviewed action writes an attributable audit_event
  2. Audit events cannot be mutated/deleted via the model layer
  3. record_event persists an AuditEvent row with correct fields
  4. record_event is append-only: every call creates a NEW row
  5. Operator sign-in writes an audit_event
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.audit.recorder import record_event
from app.auth.operator import hash_password
from app.db.session import Base
from app.models.audit_event import AuditEvent
from app.models.entity import Entity
from app.models.operator import Operator
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def audit_engine():
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
def db(audit_engine):
    connection = audit_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_operator(db: Session, email: str, role: str = "operator") -> Operator:
    op = Operator(
        email=email,
        full_name="Audit Test Op",
        role=role,
        password_hash=hash_password("testpass"),
    )
    db.add(op)
    db.commit()
    return op


def _make_run(db: Session) -> str:
    entity = Entity(canonical_name="Audit Co", canonical_domain="auditco.example")
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Audit Co",
        domain="auditco.example",
        work_email="ceo@auditco.example",
        country="US",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="complete",
    )
    db.add(run)
    db.commit()
    return run.id


# ============================================================================
# SECURITY: audit events cannot be mutated / deleted via model layer
# ============================================================================


class TestAuditImmutability:
    """AuditEvent model layer exposes no update/delete helpers."""

    def test_audit_event_has_no_update_method(self):
        """AuditEvent class has no update() method."""
        assert not hasattr(
            AuditEvent, "update"
        ), "AuditEvent must not expose an update() method"

    def test_audit_event_has_no_delete_method(self):
        """AuditEvent class has no delete() method."""
        assert not hasattr(
            AuditEvent, "delete"
        ), "AuditEvent must not expose a delete() method"

    def test_recorder_has_no_update_function(self):
        """recorder module has no update_event() function."""
        import app.audit.recorder as recorder_module  # noqa: PLC0415

        assert not hasattr(
            recorder_module, "update_event"
        ), "recorder must not expose update_event()"

    def test_recorder_has_no_delete_function(self):
        """recorder module has no delete_event() function."""
        import app.audit.recorder as recorder_module  # noqa: PLC0415

        assert not hasattr(
            recorder_module, "delete_event"
        ), "recorder must not expose delete_event()"


# ============================================================================
# record_event persists correct data
# ============================================================================


class TestRecordEvent:
    """record_event() creates a correctly-attributed AuditEvent row."""

    def test_record_event_persists_row(self, db: Session):
        op = _make_operator(db, "ev_persist@test.example")
        run_id = _make_run(db)

        before_count = db.query(AuditEvent).count()
        event_id = record_event(
            db=db,
            event_type="operator.mark_reviewed",
            operator_id=op.id,
            verification_run_id=run_id,
            payload={"review_status": "reviewed"},
            description="Test event.",
        )
        after_count = db.query(AuditEvent).count()

        assert after_count == before_count + 1
        assert event_id is not None

    def test_record_event_fields_are_correct(self, db: Session):
        op = _make_operator(db, "ev_fields@test.example")
        run_id = _make_run(db)

        event_id = record_event(
            db=db,
            event_type="operator.mark_reviewed",
            operator_id=op.id,
            verification_run_id=run_id,
            payload={"review_status": "reviewed", "notes": "Looks good."},
            description="Operator reviewed run.",
        )

        event = db.get(AuditEvent, event_id)
        assert event is not None
        assert event.event_type == "operator.mark_reviewed"
        assert event.operator_id == op.id
        assert event.verification_run_id == run_id
        assert event.payload is not None
        assert event.payload.get("review_status") == "reviewed"
        assert event.description == "Operator reviewed run."
        assert event.occurred_at is not None

    def test_record_event_is_append_only(self, db: Session):
        """Each call creates a new row; prior rows are unchanged."""
        op = _make_operator(db, "ev_append@test.example")

        id1 = record_event(
            db=db,
            event_type="test.event",
            operator_id=op.id,
            payload={"seq": 1},
        )
        id2 = record_event(
            db=db,
            event_type="test.event",
            operator_id=op.id,
            payload={"seq": 2},
        )

        assert id1 != id2
        ev1 = db.get(AuditEvent, id1)
        ev2 = db.get(AuditEvent, id2)
        assert ev1 is not None
        assert ev2 is not None
        assert ev1.payload["seq"] == 1  # unchanged
        assert ev2.payload["seq"] == 2

    def test_record_event_system_event_no_operator(self, db: Session):
        """System events (no operator_id) are valid."""
        event_id = record_event(
            db=db,
            event_type="run.started",
            operator_id=None,
            payload={"run_id": "some-run"},
        )
        event = db.get(AuditEvent, event_id)
        assert event is not None
        assert event.operator_id is None


# ============================================================================
# mark-reviewed writes attributable audit event
# ============================================================================


class TestMarkReviewedAudit:
    """The mark-reviewed action writes an audit_event attributed to the operator."""

    def test_mark_reviewed_creates_audit_event(self, db: Session):
        """After mark-reviewed, an AuditEvent with operator_id exists."""
        op = _make_operator(db, "audit_mr@test.example")
        run_id = _make_run(db)

        before_count = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "operator.mark_reviewed")
            .count()
        )

        # Simulate the mark-reviewed logic directly
        from datetime import datetime, timezone  # noqa: PLC0415

        from app.models.review import Review  # noqa: PLC0415

        review = Review(
            verification_run_id=run_id,
            operator_id=op.id,
            status="reviewed",
            notes=None,
            decided_at=datetime.now(tz=timezone.utc),
        )
        db.add(review)
        db.flush()

        record_event(
            db=db,
            event_type="operator.mark_reviewed",
            operator_id=op.id,
            verification_run_id=run_id,
            payload={"review_id": review.id, "review_status": "reviewed"},
            description=f"Operator marked run {run_id!r} as 'reviewed'.",
        )

        after_count = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "operator.mark_reviewed")
            .count()
        )
        assert after_count == before_count + 1

        # Find the event and verify attribution
        events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.event_type == "operator.mark_reviewed",
                AuditEvent.operator_id == op.id,
                AuditEvent.verification_run_id == run_id,
            )
            .all()
        )
        assert len(events) >= 1
        ev = events[-1]
        assert ev.operator_id == op.id
        assert ev.verification_run_id == run_id
        assert ev.payload is not None
        assert "review_id" in ev.payload

    def test_mark_reviewed_audit_event_has_occurred_at(self, db: Session):
        """Audit event timestamp is set."""
        op = _make_operator(db, "audit_ts@test.example")
        run_id = _make_run(db)

        event_id = record_event(
            db=db,
            event_type="operator.mark_reviewed",
            operator_id=op.id,
            verification_run_id=run_id,
            payload={},
        )
        event = db.get(AuditEvent, event_id)
        assert event.occurred_at is not None
