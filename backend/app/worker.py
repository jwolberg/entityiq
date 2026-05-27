"""Celery worker application.

Defines the Celery app and the core verification task.

In production the broker is Redis (configured via CELERY_BROKER_URL).
In tests, configure with task_always_eager=True (or use the orchestrator's
run_sync() directly) so no broker is required.
"""

import logging
import os

from celery import Celery

logger = logging.getLogger(__name__)

# Default to a local Redis instance; override via environment variable.
_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery_app = Celery(
    "entityiq",
    broker=_BROKER_URL,
    backend=_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # task_always_eager is set to True in test environments (via env var or
    # directly in test fixtures) so tasks execute synchronously in-process
    # without needing a live broker.
    task_always_eager=os.environ.get("CELERY_TASK_ALWAYS_EAGER", "false").lower()
    == "true",
)


@celery_app.task(name="entityiq.run_verification")
def run_verification_task(run_id: str) -> None:
    """Celery task: drive a VerificationRun through the pipeline.

    Opens its own DB session (from the production SessionLocal) so it can
    run in a separate worker process.  Tests that call this task via
    task_always_eager=True will also use this path.
    """
    from app.db.session import SessionLocal  # noqa: PLC0415
    from app.pipeline.orchestrator import Orchestrator, default_stages  # noqa: PLC0415

    logger.info("run_verification_task: starting run %s", run_id)
    db = SessionLocal()
    try:
        orch = Orchestrator(default_stages())
        orch.run_sync(run_id, db)
    finally:
        db.close()
    logger.info("run_verification_task: finished run %s", run_id)
