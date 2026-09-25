"""enqueue_run defers eager-mode runs until after the response (ticket 0074).

With CELERY_TASK_ALWAYS_EAGER=true (the local demo) ``.delay`` runs the whole
pipeline inline, so an endpoint that enqueues would block for the full run.
When the caller hands over a BackgroundTasks, eager dispatch is deferred to it
(it runs after the response is sent). With a real broker ``.delay`` is fast,
so dispatch stays inline and broker errors still fail the request.
"""

import unittest.mock as mock

import pytest
from fastapi import BackgroundTasks

from app.pipeline.orchestrator import enqueue_run
from app.worker import celery_app


@pytest.fixture
def delay():
    with mock.patch("app.worker.run_verification_task.delay") as m:
        yield m


def test_eager_with_background_defers_dispatch(delay, monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    background = BackgroundTasks()

    enqueue_run("run-1", background)

    delay.assert_not_called()
    assert len(background.tasks) == 1
    task = background.tasks[0]
    task.func(*task.args, **task.kwargs)
    delay.assert_called_once_with("run-1")


def test_eager_without_background_dispatches_inline(delay, monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)

    enqueue_run("run-2")

    delay.assert_called_once_with("run-2")


def test_broker_mode_dispatches_inline_even_with_background(delay, monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", False)
    background = BackgroundTasks()

    enqueue_run("run-3", background)

    delay.assert_called_once_with("run-3")
    assert background.tasks == []
