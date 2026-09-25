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
    """Ordered screening stages: block → score → dispose (0037–0039)."""
    from app.screening.dispose import DisposeStage  # noqa: PLC0415
    from app.screening.stages import (  # noqa: PLC0415
        BlockCandidatesStage,
        ScoreCandidatesStage,
    )

    return [BlockCandidatesStage(), ScoreCandidatesStage(), DisposeStage()]


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
        on_failure=_no_partial_report,
    ).run_sync(run_id, db)
