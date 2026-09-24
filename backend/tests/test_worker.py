"""Celery task time limits (ticket 0001, plan U21).

The task carries soft and hard time limits sized above the orchestrator's run
budget. If the soft limit fires anyway, the run is marked failed but the
partial report is still assembled, so the run isn't lost.
"""

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.session import Base
from app.models.entity import Entity
from app.models.report import Report
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.worker import run_verification_task


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'worker.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine, monkeypatch):
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr("app.db.session.SessionLocal", factory)
    return factory


def _make_run(factory) -> str:
    db: Session = factory()
    try:
        entity = Entity(canonical_name="Budget Ltd")
        db.add(entity)
        db.flush()
        sub = Submission(
            company_name="Budget Ltd",
            domain="budget.example",
            work_email="ops@budget.example",
            country="GB",
            entity_id=entity.id,
        )
        db.add(sub)
        db.flush()
        run = VerificationRun(
            submission_id=sub.id, entity_id=entity.id, status="pending"
        )
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


def test_task_has_soft_and_hard_time_limits():
    from app.worker import RUN_TIMEOUT_SECONDS

    soft = run_verification_task.soft_time_limit
    hard = run_verification_task.time_limit
    assert soft is not None and hard is not None
    assert RUN_TIMEOUT_SECONDS < soft < hard
    # PRD § Performance Expectations: analysis completes in under 2 hours.
    assert hard <= 2 * 60 * 60


def test_soft_time_limit_marks_run_failed_but_keeps_partial_report(
    session_factory, monkeypatch
):
    run_id = _make_run(session_factory)

    class Done:
        name = "normalize_input"

        def run(self, run_id, db, context):
            return {**context, "normalize_input": "done"}

    class HitsSoftLimit:
        name = "query_registries"

        def run(self, run_id, db, context):
            raise SoftTimeLimitExceeded()

    monkeypatch.setattr(
        "app.pipeline.orchestrator.default_stages", lambda: [Done(), HitsSoftLimit()]
    )

    run_verification_task(run_id)

    db = session_factory()
    try:
        run = db.get(VerificationRun, run_id)
        assert run.status == "failed"
        assert "time limit" in (run.failure_reason or "").lower()
        assert run.finished_at is not None
        assert run.source_availability["normalize_input"] == "complete"

        report = (
            db.query(Report).filter(Report.verification_run_id == run_id).one_or_none()
        )
        assert report is not None, "partial report must survive the time limit"
    finally:
        db.close()


def test_soft_limit_signal_while_waiting_on_a_hung_stage(session_factory, monkeypatch):
    """Celery delivers the soft limit as a signal on the main thread.

    Here it lands while the orchestrator is blocked waiting on a hung stage (not
    raised inside the stage). The stage must be abandoned so its late write
    never lands, the run is failed with a clear reason, and the partial report
    is still assembled.
    """
    import signal
    import threading
    import time

    from app.models.entity import Entity

    run_id = _make_run(session_factory)
    db = session_factory()
    entity_id = db.get(VerificationRun, run_id).entity_id
    db.close()
    release = threading.Event()
    finished = threading.Event()

    class Done:
        name = "normalize_input"

        def run(self, run_id, db, context):
            return {**context, "normalize_input": "done"}

    class Hangs:
        name = "query_registries"

        def run(self, run_id, db, context):
            try:
                release.wait(10)
                db.get(Entity, entity_id).canonical_domain = "late.example"
                db.commit()
                return context
            finally:
                finished.set()

    monkeypatch.setattr(
        "app.pipeline.orchestrator.default_stages", lambda: [Done(), Hangs()]
    )

    def fire_soft_limit(signum, frame):
        raise SoftTimeLimitExceeded()

    previous = signal.signal(signal.SIGALRM, fire_soft_limit)
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    try:
        run_verification_task(run_id)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    release.set()
    assert finished.wait(2.0)
    time.sleep(0.2)

    db = session_factory()
    try:
        run = db.get(VerificationRun, run_id)
        assert run.status == "failed"
        assert "time limit" in (run.failure_reason or "").lower()
        assert run.source_availability == {
            "normalize_input": "complete",
            "query_registries": "unavailable",
        }
        assert (
            db.query(Report).filter(Report.verification_run_id == run_id).one_or_none()
            is not None
        )
        assert db.get(Entity, entity_id).canonical_domain is None
    finally:
        db.close()
