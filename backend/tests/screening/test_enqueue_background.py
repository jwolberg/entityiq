"""enqueue_screening defers eager-mode runs past the response (ticket 0075).

Mirrors enqueue_run (ticket 0074): with CELERY_TASK_ALWAYS_EAGER=true `.delay`
runs the whole screening inline, so intake would block for the full run. With
the endpoint's BackgroundTasks, eager dispatch is deferred until after the
response; with a real broker it stays inline so broker errors still fail.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi import BackgroundTasks

from app.screening import api as screening_api
from app.screening.api import enqueue_screening
from app.worker import celery_app


@pytest.fixture
def delay():
    with mock.patch("app.screening.tasks.run_screening_task.delay") as m:
        yield m


def test_eager_with_background_defers_dispatch(delay, monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    background = BackgroundTasks()

    enqueue_screening("run-1", background)

    delay.assert_not_called()
    assert len(background.tasks) == 1
    task = background.tasks[0]
    task.func(*task.args, **task.kwargs)
    delay.assert_called_once_with("run-1")


def test_eager_without_background_dispatches_inline(delay, monkeypatch):
    # Monitoring re-screens call enqueue without a request; they stay inline.
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)

    enqueue_screening("run-2")

    delay.assert_called_once_with("run-2")


def test_broker_mode_dispatches_inline_even_with_background(delay, monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", False)
    background = BackgroundTasks()

    enqueue_screening("run-3", background)

    delay.assert_called_once_with("run-3")
    assert background.tasks == []


def test_intake_passes_background_tasks_to_enqueue(api_env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        screening_api,
        "enqueue_screening",
        lambda run_id, background=None: calls.append((run_id, background)),
    )

    r = api_env["client"].post(
        "/screenings",
        json={"name": "Ingrid Solheimsen"},
        headers=api_env["auth"]["operator"],
    )

    assert r.status_code == 201, r.text
    body = r.json()
    [(run_id, background)] = calls
    assert run_id == body["run_id"]
    assert isinstance(background, BackgroundTasks)
    # Not run yet when the response was built: pending, no disposition.
    assert body["status"] == "pending"
    assert body["disposition"] is None
