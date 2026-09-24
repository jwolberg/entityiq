"""Tests for P3-T1 / ticket 0001 — per-stage timeouts (plan U21).

A stage that runs past its time budget must not hold the whole run hostage:
the orchestrator records it as unavailable, moves on to the next stage, and
the run still completes — well inside the budget, not after the slow stage
eventually returns.

Uses a file-backed SQLite engine (not the shared-connection fixture) so each
stage gets its own connection, exactly as in production. No network access and
no Celery broker.
"""

import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.models.entity import Entity
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.pipeline.orchestrator import STAGE_COMPLETE, STAGE_UNAVAILABLE, Orchestrator

# The slow stage would take this long if nothing stopped it.
_SLOW_STAGE_SECONDS = 5.0
# The budget under test — far below the slow stage's natural duration.
_STAGE_TIMEOUT_SECONDS = 0.5


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'timeouts.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    sess = Session(bind=engine)
    yield sess
    sess.close()


def _make_run(db: Session) -> str:
    entity = Entity(canonical_name="Slowpoke Ltd")
    db.add(entity)
    db.flush()
    submission = Submission(
        company_name="Slowpoke Ltd",
        domain="slowpoke.example",
        work_email="ops@slowpoke.example",
        country="GB",
        entity_id=entity.id,
    )
    db.add(submission)
    db.flush()
    run = VerificationRun(
        submission_id=submission.id, entity_id=entity.id, status="pending"
    )
    db.add(run)
    db.commit()
    return run.id


class SlowStage:
    """Stands in for a source that hangs (e.g. a registry that never answers).

    Waits on an event rather than sleeping blindly, so the test can release it
    during teardown and no thread outlives the test.
    """

    name = "slow_source"

    def __init__(self) -> None:
        self.release = threading.Event()

    def run(self, run_id: str, db: Session, context: dict) -> dict:
        self.release.wait(_SLOW_STAGE_SECONDS)
        return {**context, "slow_source": "finished late"}


class FastStage:
    name = "fast_after"

    def run(self, run_id: str, db: Session, context: dict) -> dict:
        return {**context, "fast_after": "done"}


@pytest.fixture
def slow_stage():
    stage = SlowStage()
    yield stage
    stage.release.set()


def test_slow_stage_times_out_and_run_completes_within_budget(db, slow_stage):
    run_id = _make_run(db)
    orch = Orchestrator(
        [slow_stage, FastStage()], stage_timeout_seconds=_STAGE_TIMEOUT_SECONDS
    )

    started = time.monotonic()
    orch.run_sync(run_id, db)
    elapsed = time.monotonic() - started

    run = db.get(VerificationRun, run_id)
    assert run.status == "complete"
    assert run.source_availability["slow_source"] == STAGE_UNAVAILABLE
    # The stage after the timeout still ran.
    assert run.source_availability["fast_after"] == STAGE_COMPLETE
    # Bounded by the budget, not by the slow stage's natural duration.
    assert elapsed < _STAGE_TIMEOUT_SECONDS + 1.0


def test_timed_out_stage_output_is_discarded(db, slow_stage):
    """A late result must not leak into context once the stage was abandoned."""
    seen: dict = {}

    class CaptureStage:
        name = "capture"

        def run(self, run_id: str, db: Session, context: dict) -> dict:
            seen.update(context)
            return context

    run_id = _make_run(db)
    Orchestrator(
        [slow_stage, CaptureStage()], stage_timeout_seconds=_STAGE_TIMEOUT_SECONDS
    ).run_sync(run_id, db)

    assert "slow_source" not in seen


def test_fast_stages_are_unaffected_by_timeout(db):
    run_id = _make_run(db)
    Orchestrator([FastStage()], stage_timeout_seconds=_STAGE_TIMEOUT_SECONDS).run_sync(
        run_id, db
    )

    run = db.get(VerificationRun, run_id)
    assert run.status == "complete"
    assert run.source_availability == {"fast_after": STAGE_COMPLETE}


def test_writes_from_a_timed_out_stage_never_land(db):
    """The abandoned stage's session is rolled back, even once it wakes up."""
    run_id = _make_run(db)
    entity_id = db.get(VerificationRun, run_id).entity_id
    wrote = threading.Event()
    finished = threading.Event()

    class SlowWriter:
        """Hangs on its source, then writes its findings after waking up."""

        name = "slow_writer"

        def __init__(self) -> None:
            self.release = threading.Event()

        def run(self, run_id: str, db: Session, context: dict) -> dict:
            self.release.wait(_SLOW_STAGE_SECONDS)
            db.get(Entity, entity_id).canonical_domain = "late.example"
            db.commit()  # stages commit their own rows; must still not land
            wrote.set()
            return context

    stage = SlowWriter()
    orig_run = stage.run

    def tracked(*args):
        try:
            return orig_run(*args)
        finally:
            finished.set()

    stage.run = tracked
    Orchestrator([stage], stage_timeout_seconds=_STAGE_TIMEOUT_SECONDS).run_sync(
        run_id, db
    )
    db.expire_all()
    assert db.get(VerificationRun, run_id).source_availability == {
        "slow_writer": STAGE_UNAVAILABLE
    }

    # Let the abandoned stage wake up and write, then give its worker time to
    # roll back.
    stage.release.set()
    assert finished.wait(2.0)
    assert wrote.is_set()
    time.sleep(0.2)

    db.expire_all()
    assert db.get(Entity, entity_id).canonical_domain is None


def test_writes_from_a_stage_within_budget_are_committed(db):
    run_id = _make_run(db)
    entity_id = db.get(VerificationRun, run_id).entity_id

    class Writer:
        name = "writer"

        def run(self, run_id: str, db: Session, context: dict) -> dict:
            db.get(Entity, entity_id).canonical_domain = "ontime.example"
            return context

    Orchestrator([Writer()], stage_timeout_seconds=_STAGE_TIMEOUT_SECONDS).run_sync(
        run_id, db
    )
    db.expire_all()
    assert db.get(Entity, entity_id).canonical_domain == "ontime.example"
