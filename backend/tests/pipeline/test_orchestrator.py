"""Tests for P1-T2 — Async run orchestration skeleton.

All tests run synchronously against SQLite in-memory.  No live Redis or Celery
broker required.  The orchestrator's run_sync() method is called directly,
bypassing Celery entirely.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base
from app.models.entity import Entity
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.base import PipelineStage
from app.pipeline.orchestrator import (
    STAGE_COMPLETE,
    STAGE_UNAVAILABLE,
    Orchestrator,
    enqueue_reanalysis,
)

# ---------------------------------------------------------------------------
# Test DB fixture (scoped to module — independent of the submission tests DB)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def orch_engine():
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
def orch_session(orch_engine):
    """Transactional session that rolls back after each test."""
    connection = orch_engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)
    yield sess
    sess.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Stub stages for testing
# ---------------------------------------------------------------------------


class StageA:
    """Stub stage that succeeds and records itself in context."""

    name = "stage_a"

    def run(self, run_id: str, db: Session, context: dict) -> dict:
        return {**context, "stage_a": "done"}


class StageB:
    """Stub stage that also succeeds."""

    name = "stage_b"

    def run(self, run_id: str, db: Session, context: dict) -> dict:
        return {**context, "stage_b": "done"}


class FailingStage:
    """Stub stage that always raises — should be recorded as unavailable."""

    name = "failing_stage"

    def run(self, run_id: str, db: Session, context: dict) -> dict:
        raise RuntimeError("Simulated stage failure")


# ---------------------------------------------------------------------------
# Helper: create minimal Entity + Submission + VerificationRun
# ---------------------------------------------------------------------------


def _make_run(db: Session, status: str = "pending") -> VerificationRun:
    entity = Entity(canonical_name="Test Co", canonical_domain="test.example")
    db.add(entity)
    db.flush()

    sub = Submission(
        company_name="Test Co",
        domain="test.example",
        work_email="test@test.example",
        country="US",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()

    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status=status,
    )
    db.add(run)
    db.commit()
    return run


# ---------------------------------------------------------------------------
# Tests: two-stage happy path
# ---------------------------------------------------------------------------


def test_two_stage_run_transitions_pending_to_complete(orch_session: Session):
    """A run with two successful stages: pending → running → complete."""
    run = _make_run(orch_session)
    assert run.status == "pending"

    orch = Orchestrator([StageA(), StageB()])
    orch.run_sync(run.id, orch_session)

    refreshed = orch_session.get(VerificationRun, run.id)
    assert refreshed is not None
    assert refreshed.status == "complete"
    assert refreshed.started_at is not None
    assert refreshed.finished_at is not None


def test_two_stage_run_records_all_stages_complete(orch_session: Session):
    """Both stages should be recorded as 'complete' in source_availability."""
    run = _make_run(orch_session)
    orch = Orchestrator([StageA(), StageB()])
    orch.run_sync(run.id, orch_session)

    refreshed = orch_session.get(VerificationRun, run.id)
    assert refreshed is not None
    avail = refreshed.source_availability
    assert avail is not None
    assert avail["stage_a"] == STAGE_COMPLETE
    assert avail["stage_b"] == STAGE_COMPLETE


# ---------------------------------------------------------------------------
# Tests: raising stage → unavailable, run still completes
# ---------------------------------------------------------------------------


def test_raising_stage_is_unavailable_run_still_completes(orch_session: Session):
    """A stage that raises → 'unavailable'; the run reaches 'complete'."""
    run = _make_run(orch_session)
    orch = Orchestrator([StageA(), FailingStage(), StageB()])
    orch.run_sync(run.id, orch_session)

    refreshed = orch_session.get(VerificationRun, run.id)
    assert refreshed is not None
    assert refreshed.status == "complete"
    avail = refreshed.source_availability
    assert avail["stage_a"] == STAGE_COMPLETE
    assert avail["failing_stage"] == STAGE_UNAVAILABLE
    assert avail["stage_b"] == STAGE_COMPLETE


def test_all_stages_failing_run_still_completes(orch_session: Session):
    """Even if all stages raise, the run itself should reach 'complete'."""
    run = _make_run(orch_session)
    orch = Orchestrator([FailingStage()])
    orch.run_sync(run.id, orch_session)

    refreshed = orch_session.get(VerificationRun, run.id)
    assert refreshed is not None
    assert refreshed.status == "complete"
    assert refreshed.source_availability["failing_stage"] == STAGE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Tests: partial-result visibility mid-run
# ---------------------------------------------------------------------------


def test_stage_status_is_readable_after_each_stage(orch_session: Session):
    """source_availability is committed after each stage (partial-result support).

    We verify this by using a stage that reads source_availability from the DB
    after a prior stage has written to it.
    """
    captured: list[dict] = []

    class InspectingStage:
        name = "inspecting_stage"

        def run(self, run_id: str, db: Session, context: dict) -> dict:
            # At this point StageA has already written its status.
            run = db.get(VerificationRun, run_id)
            captured.append(dict(run.source_availability or {}))
            return context

    run = _make_run(orch_session)
    orch = Orchestrator([StageA(), InspectingStage()])
    orch.run_sync(run.id, orch_session)

    # When InspectingStage ran, stage_a should already be "complete".
    assert captured, "InspectingStage did not capture source_availability"
    assert captured[0].get("stage_a") == STAGE_COMPLETE


# ---------------------------------------------------------------------------
# Tests: re-analysis supersedes prior run
# ---------------------------------------------------------------------------


def test_reanalysis_creates_new_run_with_supersedes_id(orch_session: Session):
    """enqueue_reanalysis creates a new run that supersedes the prior one."""
    # We don't want to actually enqueue via Celery here; patch enqueue_run.
    import unittest.mock as mock

    run = _make_run(orch_session)
    run.status = "complete"
    orch_session.commit()

    with mock.patch("app.pipeline.orchestrator.enqueue_run") as mock_enqueue:
        new_run_id = enqueue_reanalysis(run.entity_id, run.id, orch_session)
        mock_enqueue.assert_called_once_with(new_run_id, None)

    new_run = orch_session.get(VerificationRun, new_run_id)
    assert new_run is not None
    assert new_run.supersedes_id == run.id
    assert new_run.status == "pending"
    assert new_run.entity_id == run.entity_id


def test_reanalysis_does_not_mutate_prior_run(orch_session: Session):
    """The superseded run is retained (not overwritten) for audit history."""
    import unittest.mock as mock

    run = _make_run(orch_session)
    run.status = "complete"
    orch_session.commit()

    with mock.patch("app.pipeline.orchestrator.enqueue_run"):
        enqueue_reanalysis(run.entity_id, run.id, orch_session)

    # Prior run still exists and still has status "complete".
    prior = orch_session.get(VerificationRun, run.id)
    assert prior is not None
    assert prior.status == "complete"


# ---------------------------------------------------------------------------
# Tests: PipelineStage protocol satisfaction
# ---------------------------------------------------------------------------


def test_stub_stages_satisfy_protocol():
    """Verify our stub stages implement the PipelineStage protocol."""
    assert isinstance(StageA(), PipelineStage)
    assert isinstance(StageB(), PipelineStage)
    assert isinstance(FailingStage(), PipelineStage)
