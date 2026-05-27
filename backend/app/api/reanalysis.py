"""Re-analysis endpoint — POST /reanalysis/{run_id} (P2-T11).

Triggers a fresh verification run for an entity, superseding the prior run.
The prior run is retained in the audit trail (ARCHITECTURE § 2).

Protected route: requires an authenticated operator (any role).
Every trigger is recorded as an audit event.

Usage:
    POST /reanalysis/{run_id}
    Authorization: Bearer <session_token>

Returns 202 with the new run_id and a pending status.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.operator import get_current_operator
from app.db.session import SessionLocal
from app.models.verification_run import VerificationRun

router = APIRouter(prefix="/reanalysis", tags=["reanalysis"])


# ---------------------------------------------------------------------------
# DB dependency
# ---------------------------------------------------------------------------


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ReanalysisResponse(BaseModel):
    new_run_id: str
    supersedes_run_id: str
    entity_id: str
    status: str
    triggered_at: str
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/{run_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReanalysisResponse,
    summary="Trigger re-analysis of an entity (supersedes the given run)",
)
def trigger_reanalysis(
    run_id: str,
    db: Session = Depends(_get_db),
    operator=Depends(get_current_operator),
) -> ReanalysisResponse:
    """Trigger a fresh verification run for an entity.

    Creates a new VerificationRun that supersedes the given run_id.
    The prior run is retained for audit history and score-change tracking.

    Returns 202 with the new run_id.
    Returns 404 if the run_id is not found.
    """
    # Verify run exists
    prior_run = db.get(VerificationRun, run_id)
    if prior_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification run {run_id!r} not found.",
        )

    from app.pipeline.orchestrator import enqueue_reanalysis  # noqa: PLC0415

    new_run_id = enqueue_reanalysis(
        entity_id=prior_run.entity_id,
        supersedes_run_id=run_id,
        db=db,
    )

    now = datetime.now(tz=timezone.utc)

    # Record audit event for re-analysis trigger
    record_event(
        db=db,
        event_type="operator.trigger_reanalysis",
        operator_id=operator.id,
        verification_run_id=new_run_id,
        submission_id=prior_run.submission_id,
        payload={
            "new_run_id": new_run_id,
            "supersedes_run_id": run_id,
            "entity_id": prior_run.entity_id,
            "triggered_at": now.isoformat(),
        },
        description=(
            f"Operator {operator.email!r} triggered re-analysis; "
            f"new run {new_run_id!r} supersedes {run_id!r}."
        ),
    )

    return ReanalysisResponse(
        new_run_id=new_run_id,
        supersedes_run_id=run_id,
        entity_id=prior_run.entity_id,
        status="pending",
        triggered_at=now.isoformat(),
        message="Re-analysis enqueued. New run supersedes the prior run.",
    )
