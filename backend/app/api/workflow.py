"""Operator workflow endpoints (P2-T11).

Endpoints for operator actions beyond mark-reviewed:

  POST /workflow/runs/{run_id}/correct   — correct submitted fields + re-run
  POST /workflow/runs/{run_id}/notes     — add/update review notes

Both routes are operator-authenticated and audited.  Corrections record the
changed fields before/after in the audit payload.  Corrections trigger a new
re-analysis run that supersedes the current run (the prior run is retained
for audit history — ARCHITECTURE § 2, § 6).

Notes: upsert — adds notes if no review row exists yet, or appends to
existing notes if one does.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.operator import get_current_operator
from app.db.session import SessionLocal
from app.models.review import Review
from app.models.submission import Submission
from app.models.verification_run import VerificationRun

router = APIRouter(prefix="/workflow", tags=["workflow"])


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


class CorrectAndRerunRequest(BaseModel):
    """Body for POST /workflow/runs/{run_id}/correct.

    corrections: mapping of field_name → corrected_value for the fields
    being overridden.  Only the listed fields are updated; others are unchanged.

    Correctable fields: company_name, domain, work_email, country, tax_id,
    billing_address, phone, requester_full_name, linkedin_url.

    After applying corrections a new re-analysis run is enqueued that
    supersedes the given run_id.
    """

    corrections: dict[str, str | None]
    notes: str | None = None


class CorrectAndRerunResponse(BaseModel):
    new_run_id: str
    supersedes_run_id: str
    submission_id: str
    corrections_applied: dict[str, str | None]
    triggered_at: str
    message: str


class AddNotesRequest(BaseModel):
    """Body for POST /workflow/runs/{run_id}/notes."""

    notes: str
    # Optional review status update (leave None to keep existing status)
    review_status: str | None = None


class AddNotesResponse(BaseModel):
    review_id: str
    run_id: str
    notes: str
    review_status: str
    updated_at: str
    message: str


# ---------------------------------------------------------------------------
# Correctable field names → Submission column names
# ---------------------------------------------------------------------------

_CORRECTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "company_name",
        "domain",
        "work_email",
        "country",
        "tax_id",
        "billing_address",
        "phone",
        "requester_full_name",
        "linkedin_url",
    }
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/runs/{run_id}/correct",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CorrectAndRerunResponse,
    summary="Correct submitted fields and re-run analysis",
)
def correct_and_rerun(
    run_id: str,
    body: CorrectAndRerunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_get_db),
    operator=Depends(get_current_operator),
) -> CorrectAndRerunResponse:
    """Correct submitted fields on a run and trigger re-analysis.

    - Validates correctable field names.
    - Records before/after values in the audit event.
    - Applies corrections to the Submission row (mutable correction — the
      original immutable submission is supplemented by the Review.corrections
      JSON column which is also updated).
    - Triggers a new VerificationRun that supersedes run_id.
    - Returns 202 with the new run_id.

    Returns 404 if run not found.
    Returns 422 if corrections contain non-correctable field names.
    """
    if not body.corrections:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="corrections must not be empty.",
        )

    invalid_fields = set(body.corrections.keys()) - _CORRECTABLE_FIELDS
    if invalid_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Non-correctable fields: {sorted(invalid_fields)}. "
                f"Allowed fields: {sorted(_CORRECTABLE_FIELDS)}."
            ),
        )

    prior_run = db.get(VerificationRun, run_id)
    if prior_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification run {run_id!r} not found.",
        )

    submission = db.get(Submission, prior_run.submission_id)
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Submission for run {run_id!r} not found.",
        )

    # Capture before values for the audit record
    before_values: dict[str, str | None] = {
        field: getattr(submission, field, None) for field in body.corrections
    }

    # Apply corrections to the Submission row
    for field, new_value in body.corrections.items():
        setattr(submission, field, new_value)

    # Upsert corrections into the Review row (create if it doesn't exist)
    existing_review = (
        db.query(Review).filter(Review.verification_run_id == run_id).first()
    )
    now = datetime.now(tz=timezone.utc)
    if existing_review is not None:
        merged = dict(existing_review.corrections or {})
        merged.update(body.corrections)
        existing_review.corrections = merged
        if body.notes:
            existing_review.notes = (
                (existing_review.notes or "") + "\n" + body.notes
            ).strip()
        existing_review.updated_at = now
    else:
        new_review = Review(
            verification_run_id=run_id,
            operator_id=operator.id,
            status="reviewed",
            notes=body.notes,
            corrections=body.corrections,
            decided_at=now,
        )
        db.add(new_review)

    db.flush()

    # Enqueue re-analysis that supersedes this run
    from app.pipeline.orchestrator import enqueue_reanalysis  # noqa: PLC0415

    new_run_id = enqueue_reanalysis(
        entity_id=prior_run.entity_id,
        supersedes_run_id=run_id,
        db=db,
        background=background_tasks,
    )

    # Record audit event with full before/after diff
    record_event(
        db=db,
        event_type="operator.correct_and_rerun",
        operator_id=operator.id,
        verification_run_id=new_run_id,
        submission_id=prior_run.submission_id,
        payload={
            "new_run_id": new_run_id,
            "supersedes_run_id": run_id,
            "submission_id": prior_run.submission_id,
            "corrections": body.corrections,
            "before": before_values,
            "notes": body.notes,
            "triggered_at": now.isoformat(),
        },
        description=(
            f"Operator {operator.email!r} corrected fields "
            f"{sorted(body.corrections.keys())} on submission "
            f"{prior_run.submission_id!r} and triggered re-analysis; "
            f"new run {new_run_id!r} supersedes {run_id!r}."
        ),
    )

    return CorrectAndRerunResponse(
        new_run_id=new_run_id,
        supersedes_run_id=run_id,
        submission_id=prior_run.submission_id,
        corrections_applied=body.corrections,
        triggered_at=now.isoformat(),
        message="Corrections applied and re-analysis enqueued.",
    )


@router.post(
    "/runs/{run_id}/notes",
    status_code=status.HTTP_200_OK,
    response_model=AddNotesResponse,
    summary="Add or update review notes for a run",
)
def add_notes(
    run_id: str,
    body: AddNotesRequest,
    db: Session = Depends(_get_db),
    operator=Depends(get_current_operator),
) -> AddNotesResponse:
    """Add notes to an existing review, or create a review row with notes.

    If a Review row exists for the run, the notes are appended.
    If no Review row exists yet, one is created with status "reviewed".

    The review_status field in the request body allows optionally updating
    the review status at the same time as adding notes.

    All note additions are audited.

    Returns 404 if the run is not found.
    """
    run = db.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification run {run_id!r} not found.",
        )

    now = datetime.now(tz=timezone.utc)

    existing_review = (
        db.query(Review).filter(Review.verification_run_id == run_id).first()
    )

    if existing_review is not None:
        # Append notes (newline-separated)
        existing_notes = existing_review.notes or ""
        updated_notes = (existing_notes + "\n" + body.notes).strip()
        existing_review.notes = updated_notes
        if body.review_status is not None:
            existing_review.status = body.review_status
        existing_review.updated_at = now
        db.flush()
        review = existing_review
    else:
        review_status = body.review_status or "reviewed"
        review = Review(
            verification_run_id=run_id,
            operator_id=operator.id,
            status=review_status,
            notes=body.notes,
            corrections=None,
            decided_at=now,
        )
        db.add(review)
        db.flush()

    record_event(
        db=db,
        event_type="operator.add_notes",
        operator_id=operator.id,
        verification_run_id=run_id,
        submission_id=run.submission_id,
        payload={
            "review_id": review.id,
            "notes_added": body.notes,
            "review_status": review.status,
            "updated_at": now.isoformat(),
        },
        description=(f"Operator {operator.email!r} added notes to run {run_id!r}."),
    )

    return AddNotesResponse(
        review_id=review.id,
        run_id=run_id,
        notes=review.notes or "",
        review_status=review.status,
        updated_at=now.isoformat(),
        message="Notes added successfully.",
    )
