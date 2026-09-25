"""Tests for the PII retention job (ADR-0002; tickets 0002, 0020).

Covers the retention windows and anonymization rules from
docs/decisions/0002-pii-retention-policy.md:

  - Network metadata (source_ip/user_agent/forwarded_headers) is truncated/
    nulled after ENTITYIQ_RETENTION_NETWORK_DAYS.
  - Submitted PII on a reviewed submission is nulled after
    ENTITYIQ_RETENTION_REVIEWED_DAYS from the review decision.
  - Submitted PII on a never-reviewed submission is nulled after
    ENTITYIQ_RETENTION_UNREVIEWED_DAYS from submission.
  - Rows are anonymized in place, never hard-deleted.
  - The audit log is untouched, and the job writes exactly one audit event
    with counts per category.
  - The job is idempotent.
  - Requester-association evidence (ticket 0020) is scrubbed on the same
    submitted-PII schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register all ORM models
from app.audit.recorder import record_event
from app.db.retention import ANONYMIZED_EMAIL, run_retention
from app.db.session import Base
from app.models.audit_event import AuditEvent
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.operator import Operator
from app.models.review import Review
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(engine):
    sess = Session(bind=engine)
    yield sess
    sess.close()


def _entity(db: Session, *, name: str) -> Entity:
    entity = Entity(canonical_name=name, canonical_domain=f"{name.lower()}.example")
    db.add(entity)
    db.flush()
    return entity


def _submission(
    db: Session,
    *,
    entity: Entity,
    submitted_at: datetime,
    source_ip: str | None = "73.162.40.18",
    user_agent: str | None = "Mozilla/5.0 (demo)",
    forwarded_headers: dict | None = None,
) -> Submission:
    sub = Submission(
        company_name=entity.canonical_name,
        domain=entity.canonical_domain,
        work_email="requester@example.com",
        country="US",
        tax_id="12-3456789",
        billing_address="123 Main St, Springfield",
        phone="+1-555-0100",
        requester_full_name="Jane Smith",
        linkedin_url="https://www.linkedin.com/in/jane-smith",
        entity_id=entity.id,
        source_ip=source_ip,
        user_agent=user_agent,
        forwarded_headers=forwarded_headers or {"x-forwarded-for": source_ip or ""},
        submitted_at=submitted_at,
    )
    db.add(sub)
    db.flush()
    return sub


def _run(db: Session, *, submission: Submission) -> VerificationRun:
    run = VerificationRun(
        submission_id=submission.id, entity_id=submission.entity_id, status="complete"
    )
    db.add(run)
    db.flush()
    return run


def _review(db: Session, *, run: VerificationRun, decided_at: datetime) -> Review:
    op = Operator(email=f"op-{run.id}@example.com", full_name="Op", role="operator")
    db.add(op)
    db.flush()
    review = Review(
        verification_run_id=run.id,
        operator_id=op.id,
        status="approved",
        decided_at=decided_at,
    )
    db.add(review)
    db.flush()
    return review


def _linkedin_evidence(db: Session, *, run: VerificationRun) -> Evidence:
    ev = Evidence(
        verification_run_id=run.id,
        source="linkedin",
        tier=3,
        field="linkedin_presence",
        raw_value="found",
        normalized_value="found",
        confidence=0.7,
        raw_payload={
            "url": "https://www.linkedin.com/company/acme-corp",
            "name": "Acme Corporation",
            "associated_people": ["Jane Smith"],
            "resolved_by": "url",
        },
        attribution={"provider": "stub"},
    )
    db.add(ev)
    match_ev = Evidence(
        verification_run_id=run.id,
        source="linkedin",
        tier=3,
        field="linkedin_requester_match",
        raw_value="true",
        normalized_value="true",
        confidence=0.6,
        raw_payload={},
        attribution={"provider": "stub"},
    )
    db.add(match_ev)
    db.flush()
    return ev


# ---------------------------------------------------------------------------
# Network metadata
# ---------------------------------------------------------------------------


def test_network_metadata_untouched_within_window(db):
    entity = _entity(db, name="Recent")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=10))
    db.commit()

    run_retention(db, now=NOW)

    db.refresh(sub)
    assert sub.source_ip == "73.162.40.18"
    assert sub.user_agent == "Mozilla/5.0 (demo)"
    assert sub.forwarded_headers is not None


def test_network_metadata_anonymized_ipv4_past_window(db):
    entity = _entity(db, name="OldNet4")
    sub = _submission(
        db,
        entity=entity,
        submitted_at=NOW - timedelta(days=91),
        source_ip="73.162.40.18",
    )
    db.commit()

    counts = run_retention(db, now=NOW)

    db.refresh(sub)
    assert sub.source_ip == "73.162.40.0/24"
    assert sub.user_agent is None
    assert sub.forwarded_headers is None
    assert counts["network_metadata"] == 1


def test_network_metadata_anonymized_ipv6_past_window(db):
    entity = _entity(db, name="OldNet6")
    sub = _submission(
        db,
        entity=entity,
        submitted_at=NOW - timedelta(days=91),
        source_ip="2001:db8::1234",
    )
    db.commit()

    run_retention(db, now=NOW)

    db.refresh(sub)
    assert sub.source_ip == "2001:db8::/48"


def test_network_metadata_anonymization_is_idempotent(db):
    entity = _entity(db, name="Idem")
    sub = _submission(
        db,
        entity=entity,
        submitted_at=NOW - timedelta(days=91),
        source_ip="73.162.40.18",
    )
    db.commit()

    run_retention(db, now=NOW)
    db.refresh(sub)
    first_ip = sub.source_ip

    counts = run_retention(db, now=NOW)
    db.refresh(sub)

    assert sub.source_ip == first_ip
    assert counts["network_metadata"] == 0


# ---------------------------------------------------------------------------
# Submitted PII — reviewed schedule
# ---------------------------------------------------------------------------


def test_reviewed_submission_pii_kept_within_window(db):
    entity = _entity(db, name="ReviewedRecent")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=2000))
    run = _run(db, submission=sub)
    _review(db, run=run, decided_at=NOW - timedelta(days=100))
    db.commit()

    run_retention(db, now=NOW)

    db.refresh(sub)
    assert sub.requester_full_name == "Jane Smith"
    assert sub.work_email != ANONYMIZED_EMAIL


def test_reviewed_submission_pii_anonymized_past_window(db):
    entity = _entity(db, name="ReviewedOld")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=2000))
    run = _run(db, submission=sub)
    _review(db, run=run, decided_at=NOW - timedelta(days=1826))
    db.commit()

    counts = run_retention(db, now=NOW)

    db.refresh(sub)
    assert sub.tax_id is None
    assert sub.billing_address is None
    assert sub.phone is None
    assert sub.requester_full_name is None
    assert sub.linkedin_url is None
    assert sub.work_email == ANONYMIZED_EMAIL
    # Company-level fields are preserved (ADR-0002 §2).
    assert sub.company_name == "ReviewedOld"
    assert sub.domain == "reviewedold.example"
    assert sub.country == "US"
    assert counts["reviewed_pii"] == 1
    assert counts["unreviewed_pii"] == 0


# ---------------------------------------------------------------------------
# Submitted PII — unreviewed schedule
# ---------------------------------------------------------------------------


def test_unreviewed_submission_pii_kept_within_window(db):
    entity = _entity(db, name="UnreviewedRecent")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=50))
    db.commit()

    run_retention(db, now=NOW)

    db.refresh(sub)
    assert sub.requester_full_name == "Jane Smith"


def test_unreviewed_submission_pii_anonymized_past_window(db):
    entity = _entity(db, name="UnreviewedOld")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=181))
    db.commit()

    counts = run_retention(db, now=NOW)

    db.refresh(sub)
    assert sub.requester_full_name is None
    assert sub.work_email == ANONYMIZED_EMAIL
    assert counts["unreviewed_pii"] == 1
    assert counts["reviewed_pii"] == 0


def test_pii_anonymization_is_idempotent(db):
    entity = _entity(db, name="IdemPii")
    _submission(db, entity=entity, submitted_at=NOW - timedelta(days=181))
    db.commit()

    run_retention(db, now=NOW)
    counts = run_retention(db, now=NOW)

    assert counts["unreviewed_pii"] == 0
    assert counts["reviewed_pii"] == 0


# ---------------------------------------------------------------------------
# Requester-association evidence (ticket 0020)
# ---------------------------------------------------------------------------


def test_requester_association_scrubbed_with_submitted_pii(db):
    entity = _entity(db, name="AssocOld")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=181))
    run = _run(db, submission=sub)
    presence_ev = _linkedin_evidence(db, run=run)
    db.commit()
    presence_id = presence_ev.id

    counts = run_retention(db, now=NOW)

    db.refresh(presence_ev)
    assert "associated_people" not in (presence_ev.raw_payload or {})
    # The rest of the linkedin_presence payload (company-level) is preserved.
    assert presence_ev.raw_payload["name"] == "Acme Corporation"
    assert counts["requester_association"] == 1

    match_ev = (
        db.query(Evidence)
        .filter(
            Evidence.verification_run_id == run.id,
            Evidence.field == "linkedin_requester_match",
        )
        .one()
    )
    # The true/false match flag itself is a derived fact, not raw PII — kept.
    assert match_ev.normalized_value == "true"
    assert presence_id == presence_ev.id


def test_requester_association_not_touched_within_window(db):
    entity = _entity(db, name="AssocRecent")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=5))
    run = _run(db, submission=sub)
    presence_ev = _linkedin_evidence(db, run=run)
    db.commit()

    counts = run_retention(db, now=NOW)

    db.refresh(presence_ev)
    assert "associated_people" in presence_ev.raw_payload
    assert counts["requester_association"] == 0


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_run_writes_one_audit_event_with_counts(db):
    entity = _entity(db, name="AuditCounts")
    _submission(db, entity=entity, submitted_at=NOW - timedelta(days=181))
    db.commit()

    run_retention(db, now=NOW)

    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.event_type == "retention.anonymized")
        .all()
    )
    assert len(events) == 1
    payload = events[0].payload
    assert payload["unreviewed_pii"] == 1
    assert payload["network_metadata"] == 1


def test_no_op_run_writes_no_audit_event(db):
    entity = _entity(db, name="NoOp")
    _submission(db, entity=entity, submitted_at=NOW - timedelta(days=1))
    db.commit()

    run_retention(db, now=NOW)

    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.event_type == "retention.anonymized")
        .all()
    )
    assert events == []


def test_audit_log_is_untouched_by_anonymization(db):
    entity = _entity(db, name="AuditUntouched")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=181))
    run = _run(db, submission=sub)
    record_event(
        db,
        "operator.mark_reviewed",
        verification_run_id=run.id,
        submission_id=sub.id,
        payload={"notes": "Jane Smith confirmed by phone, +1-555-0100"},
        description="Reviewed by Jane Smith",
    )
    db.commit()

    pre = (
        db.query(AuditEvent)
        .filter(AuditEvent.event_type == "operator.mark_reviewed")
        .one()
    )
    pre_payload = dict(pre.payload)
    pre_description = pre.description

    run_retention(db, now=NOW)

    post = (
        db.query(AuditEvent)
        .filter(AuditEvent.event_type == "operator.mark_reviewed")
        .one()
    )
    assert post.payload == pre_payload
    assert post.description == pre_description


# ---------------------------------------------------------------------------
# Env-configurable windows
# ---------------------------------------------------------------------------


def test_windows_are_env_configurable(db, monkeypatch):
    monkeypatch.setenv("ENTITYIQ_RETENTION_UNREVIEWED_DAYS", "10")
    entity = _entity(db, name="EnvWindow")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=11))
    db.commit()

    run_retention(db, now=NOW)

    db.refresh(sub)
    assert sub.requester_full_name is None


def test_association_on_a_new_run_after_anonymization_is_still_scrubbed(db):
    """Review finding: re-analysis after PII was anonymized creates fresh
    LinkedIn evidence; a later pass must scrub it too."""
    entity = _entity(db, name="AssocReanalyzed")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=181))
    db.commit()
    run_retention(db, now=NOW)  # anonymizes the submission's PII

    later_run = _run(db, submission=sub)  # re-analysis after anonymization
    presence_ev = _linkedin_evidence(db, run=later_run)
    db.commit()

    counts = run_retention(db, now=NOW + timedelta(days=1))

    db.refresh(presence_ev)
    assert "associated_people" not in (presence_ev.raw_payload or {})
    assert counts["requester_association"] == 1
    assert run_retention(db, now=NOW + timedelta(days=2))["requester_association"] == 0


def test_unparsable_ip_is_dropped_and_the_row_settles(db):
    """Review finding: a malformed IP was left raw and re-flagged forever."""
    entity = _entity(db, name="BadIp")
    sub = _submission(
        db, entity=entity, submitted_at=NOW - timedelta(days=91), source_ip="not-an-ip"
    )
    db.commit()

    first = run_retention(db, now=NOW)
    db.refresh(sub)
    assert sub.source_ip is None
    assert first["network_metadata"] == 1
    assert run_retention(db, now=NOW)["network_metadata"] == 0


def test_declared_people_nulled_with_submitted_pii(db):
    """Declared officers/owners are submitted PII (ticket 0078)."""
    entity = _entity(db, name="DeclaredOld")
    sub = _submission(db, entity=entity, submitted_at=NOW - timedelta(days=181))
    sub.declared_people = [{"name": "Jane Smith", "relationship": "owner"}]
    fresh = _submission(
        db, entity=_entity(db, name="DeclaredNew"), submitted_at=NOW - timedelta(days=1)
    )
    fresh.declared_people = [{"name": "Ann Lee", "relationship": "officer"}]
    db.commit()

    run_retention(db, now=NOW)

    db.refresh(sub)
    db.refresh(fresh)
    assert sub.declared_people is None
    assert fresh.declared_people == [{"name": "Ann Lee", "relationship": "officer"}]
