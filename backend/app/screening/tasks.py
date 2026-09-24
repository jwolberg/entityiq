"""Celery task for screening runs (IS1-T7, ticket 0035; PRD-IDV N3, C8).

Screening has its own, much tighter budgets than KYB (seconds, not an
hour) and runs on its own queue so long KYB runs can't starve it. Run a
worker for it with:

    celery -A app.worker worker -Q screening
"""

from __future__ import annotations

import logging
import os

from app.worker import celery_app

logger = logging.getLogger(__name__)

STAGE_TIMEOUT_SECONDS = float(
    os.environ.get("ENTITYIQ_SCREENING_STAGE_TIMEOUT_SECONDS", "10")
)
RUN_TIMEOUT_SECONDS = float(
    os.environ.get("ENTITYIQ_SCREENING_RUN_TIMEOUT_SECONDS", "60")
)
_SOFT_TIME_LIMIT = int(RUN_TIMEOUT_SECONDS + 30)
_HARD_TIME_LIMIT = _SOFT_TIME_LIMIT + 15

QUEUE = "screening"


@celery_app.task(
    name="entityiq.run_screening",
    soft_time_limit=_SOFT_TIME_LIMIT,
    time_limit=_HARD_TIME_LIMIT,
)
def run_screening_task(run_id: str) -> None:
    """Drive a ScreeningRun through the screening pipeline."""
    from app.db.session import SessionLocal  # noqa: PLC0415
    from app.screening.pipeline import run_screening  # noqa: PLC0415

    db = SessionLocal()
    try:
        run_screening(
            run_id,
            db,
            stage_timeout_seconds=STAGE_TIMEOUT_SECONDS,
            run_timeout_seconds=RUN_TIMEOUT_SECONDS,
        )
    finally:
        db.close()
