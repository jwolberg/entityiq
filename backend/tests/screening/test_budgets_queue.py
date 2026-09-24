"""Per-offering run budgets and a dedicated screening queue (IS1-T7, ticket 0035)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.pipeline.orchestrator import Orchestrator
from app.screening import tasks as screening_tasks
from app.screening.models import ScreeningRun, ScreeningSubject
from app.worker import (
    RUN_TIMEOUT_SECONDS as KYB_RUN_TIMEOUT,
)
from app.worker import (
    celery_app,
    run_verification_task,
)


def test_screening_task_has_its_own_tight_limits():
    task = screening_tasks.run_screening_task
    assert screening_tasks.RUN_TIMEOUT_SECONDS < task.soft_time_limit < task.time_limit
    # PRD-IDV N3: structured screening in seconds, full async in minutes.
    assert screening_tasks.STAGE_TIMEOUT_SECONDS <= 30
    assert task.time_limit <= 10 * 60


def test_kyb_task_limits_are_unchanged():
    assert run_verification_task.soft_time_limit > KYB_RUN_TIMEOUT
    assert KYB_RUN_TIMEOUT > screening_tasks.RUN_TIMEOUT_SECONDS


def test_screening_tasks_route_to_the_screening_queue():
    routes = celery_app.conf.task_routes
    assert routes["entityiq.run_screening"] == {"queue": "screening"}
    # KYB keeps the default queue.
    assert "entityiq.run_verification" not in routes


def test_budgets_come_from_env(monkeypatch):
    import importlib

    monkeypatch.setenv("ENTITYIQ_SCREENING_RUN_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("ENTITYIQ_SCREENING_STAGE_TIMEOUT_SECONDS", "7")
    reloaded = importlib.reload(screening_tasks)
    try:
        assert reloaded.RUN_TIMEOUT_SECONDS == 45
        assert reloaded.STAGE_TIMEOUT_SECONDS == 7
    finally:
        monkeypatch.delenv("ENTITYIQ_SCREENING_RUN_TIMEOUT_SECONDS")
        monkeypatch.delenv("ENTITYIQ_SCREENING_STAGE_TIMEOUT_SECONDS")
        importlib.reload(screening_tasks)


def test_orchestrator_drives_a_screening_run(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'o.db'}")
    Base.metadata.create_all(engine)
    db = Session(bind=engine)
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    run = ScreeningRun(subject_id=subject.id)
    db.add(run)
    db.commit()

    class Step:
        name = "normalize_subject"

        def run(self, run_id, db, context):
            return {**context, "ok": True}

    Orchestrator([Step()], run_model=ScreeningRun).run_sync(run.id, db)

    db.refresh(run)
    assert run.status == "complete"
    assert run.source_availability == {"normalize_subject": "complete"}
    assert run.started_at is not None and run.finished_at is not None
    db.close()
    engine.dispose()
