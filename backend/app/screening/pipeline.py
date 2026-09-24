"""Screening pipeline entry point (tickets 0035+).

Stages are added as they're built: normalize → block → score → dispose.
Each is a PipelineStage run by the shared orchestrator against a
ScreeningRun, using the screening budgets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.pipeline.orchestrator import Orchestrator
from app.screening.models import ScreeningRun

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.pipeline.base import PipelineStage


def screening_stages() -> list["PipelineStage"]:
    """Ordered screening stages (filled in by tickets 0036–0039)."""
    return []


def _no_partial_report(run_id: str, db: "Session") -> None:
    """Screening has no report to assemble; the failed run is the record."""


def run_screening(
    run_id: str,
    db: "Session",
    *,
    stage_timeout_seconds: float | None = None,
    run_timeout_seconds: float | None = None,
    stages: list["PipelineStage"] | None = None,
) -> None:
    Orchestrator(
        stages if stages is not None else screening_stages(),
        stage_timeout_seconds=stage_timeout_seconds,
        run_timeout_seconds=run_timeout_seconds,
        run_model=ScreeningRun,
        on_time_limit=_no_partial_report,
    ).run_sync(run_id, db)
