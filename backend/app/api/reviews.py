"""Mark-reviewed endpoint — POST /reviews/{run_id} (P1-T9).

Protected route: requires an authenticated operator (any role).
Writes a Review row + an attributable AuditEvent for every successful call.

Roles:
  - "operator" and "lead" may mark a run as reviewed.
  - No lead-only actions on this endpoint (lead-only routes are P2+).

The operator web app (P1-T10) will call this endpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.operator import get_current_operator
from app.db.session import SessionLocal
from app.models.review import Review
from app.models.verification_run import VerificationRun

router = APIRouter(prefix="/reviews", tags=["reviews"])


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


class MarkReviewedRequest(BaseModel):
    """Body for POST /reviews/{run_id}."""

    notes: str | None = None
    # "reviewed" | "approved" | "rejected" | "escalated"
    # Default "reviewed" — the minimal mark-reviewed action.
    review_status: str = "reviewed"


class ReviewResponse(BaseModel):
    review_id: str
    run_id: str
    operator_id: str
    review_status: str
    decided_at: str
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/{run_id}",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mark a verification run as reviewed",
)
def mark_reviewed(
    run_id: str,
    body: MarkReviewedRequest,
    db: Session = Depends(_get_db),
    operator=Depends(get_current_operator),
) -> ReviewResponse:
    """Mark a verification run as reviewed by the authenticated operator.

    Writes a Review row and an attributable audit_event.
    Any authenticated operator (operator or lead role) may call this.

    Returns 404 if the run does not exist.
    Returns 409 if the run has already been reviewed (idempotency guard).
    """
    # Check run exists
    run = db.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification run {run_id!r} not found.",
        )

    # Idempotency guard — a run may only have one Review (unique FK in schema)
    existing_review = (
        db.query(Review).filter(Review.verification_run_id == run_id).first()
    )
    if existing_review is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run {run_id!r} has already been reviewed "
                f"(review_id={existing_review.id!r})."
            ),
        )

    now = datetime.now(tz=timezone.utc)

    # Persist the review
    review = Review(
        verification_run_id=run_id,
        operator_id=operator.id,
        status=body.review_status,
        notes=body.notes,
        decided_at=now,
    )
    db.add(review)
    db.flush()  # assigns review.id before audit event

    # Record attributable audit event — ALWAYS before final commit
    record_event(
        db=db,
        event_type="operator.mark_reviewed",
        operator_id=operator.id,
        verification_run_id=run_id,
        submission_id=run.submission_id,
        payload={
            "review_id": review.id,
            "review_status": body.review_status,
            "notes": body.notes,
            "decided_at": now.isoformat(),
        },
        description=(
            f"Operator {operator.email!r} marked run {run_id!r} "
            f"as '{body.review_status}'."
        ),
    )

    return ReviewResponse(
        review_id=review.id,
        run_id=run_id,
        operator_id=operator.id,
        review_status=body.review_status,
        decided_at=now.isoformat(),
        message=f"Run marked as '{body.review_status}'.",
    )
