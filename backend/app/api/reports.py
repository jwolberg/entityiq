"""Report retrieval endpoint — GET /reports/{run_id} (P1-T8).

Returns the normalized report for a verification run, including:
  - scores (four-layer + overall)
  - evidence (all rows with source attribution)
  - mismatches (field comparisons — match/mismatch/unverified)
  - sources (distinct sources used with counts)

Each section carries a status (pending / complete / unavailable) so partial
(in-progress) results are readable mid-pipeline.

Routes:
  GET /reports/{run_id}  — retrieve report by verification run ID
  GET /reports/          — list recent reports (thin list; scores + run_id only)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit.recorder import record_event
from app.auth.pii import filter_summary_for_viewer, report_contains_pii, resolve_viewer
from app.auth.service import Principal, get_principal
from app.db.session import SessionLocal
from app.models.report import Report
from app.models.review import Review
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.schemas.report import (
    EvidenceItemSchema,
    MismatchItemSchema,
    ReportListItemSchema,
    ReportListResponse,
    ReportResponse,
    ReviewSummarySchema,
    RunTimingSchema,
    ScoresSchema,
    SectionStatuses,
    SourceSummarySchema,
)

router = APIRouter(prefix="/reports", tags=["reports"])


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
# Serialization helpers
# ---------------------------------------------------------------------------


def _review_summary(db: Session, run_id: str) -> ReviewSummarySchema | None:
    review = (
        db.query(Review)
        .filter(Review.verification_run_id == run_id)
        .order_by(Review.created_at.desc())
        .first()
    )
    if review is None:
        return None
    decided = review.decided_at or review.updated_at or review.created_at
    return ReviewSummarySchema(
        status=review.status,
        notes=review.notes,
        reviewer_name=review.operator.full_name if review.operator else None,
        decided_at=decided.isoformat() if decided else None,
    )


def _run_timing(run: VerificationRun) -> RunTimingSchema:
    duration = None
    if run.started_at is not None and run.finished_at is not None:
        duration = (run.finished_at - run.started_at).total_seconds()
    return RunTimingSchema(
        status=run.status,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        duration_seconds=duration,
        stages=dict(run.source_availability or {}),
    )


def _serialize_report(
    report: Report,
    review: ReviewSummarySchema | None = None,
    run: VerificationRun | None = None,
    *,
    viewer: str,
) -> ReportResponse:
    """Convert a Report ORM row + its summary dict into a ReportResponse.

    The summary is pre-assembled by StoreReportStage and stored as JSON.
    We read it here rather than re-joining all tables on every API call.

    `viewer` ("lead" | "operator" | "system", from app.auth.pii.resolve_viewer)
    is required — every call site must pick a viewer explicitly rather than
    fall back to an implicit "full access" default (ADR-0002 §2).
    """
    summary = filter_summary_for_viewer(report.summary or {}, viewer)
    ss = report.section_statuses or {}

    section_statuses = SectionStatuses(
        scores=ss.get("scores", "pending"),
        evidence=ss.get("evidence", "pending"),
        mismatches=ss.get("mismatches", "pending"),
        sources=ss.get("sources", "pending"),
    )

    # Scores section
    scores_data = summary.get("scores")
    scores = None
    if scores_data is not None:
        scores = ScoresSchema(
            overall_score=scores_data.get("overall_score"),
            entity_score=scores_data.get("entity_score"),
            infrastructure_score=scores_data.get("infrastructure_score"),
            representation_score=scores_data.get("representation_score"),
            risk_score=scores_data.get("risk_score"),
            triage_tier=scores_data.get("triage_tier"),
            contributing_signals=scores_data.get("contributing_signals", []),
        )

    # Evidence section
    evidence = [
        EvidenceItemSchema(
            id=ev["id"],
            source=ev["source"],
            tier=ev["tier"],
            field=ev.get("field"),
            raw_value=ev.get("raw_value"),
            normalized_value=ev.get("normalized_value"),
            confidence=ev.get("confidence"),
            attribution=ev.get("attribution"),
            fetched_at=ev.get("fetched_at"),
        )
        for ev in summary.get("evidence", [])
    ]

    # Mismatches section
    mismatches = [
        MismatchItemSchema(
            id=fc["id"],
            field_name=fc["field_name"],
            submitted_value=fc.get("submitted_value"),
            discovered_value=fc.get("discovered_value"),
            match_status=fc["match_status"],
            evidence_id=fc.get("evidence_id"),
        )
        for fc in summary.get("mismatches", [])
    ]

    # Sources section
    sources = [
        SourceSummarySchema(
            source=s["source"],
            tier=s["tier"],
            evidence_count=s["evidence_count"],
            attribution=s.get("attribution"),
            status=s.get("status", "available"),
        )
        for s in summary.get("sources", [])
    ]

    generated_at = report.generated_at.isoformat() if report.generated_at else None

    return ReportResponse(
        run_id=report.verification_run_id,
        report_id=report.id,
        status=report.status,
        section_statuses=section_statuses,
        scores=scores,
        evidence=evidence,
        mismatches=mismatches,
        sources=sources,
        generated_at=generated_at,
        review=review,
        run=_run_timing(run) if run is not None else None,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ReportListResponse,
    summary="List all reports (operator dashboard)",
)
def list_reports(
    triage_tier: str | None = None,
    db: Session = Depends(_get_db),
    _principal: Principal = Depends(get_principal),
) -> ReportListResponse:
    """Return a summary list of all reports for the operator dashboard.

    Each item includes company name, domain, report status, overall risk
    score, triage tier, review status, and analysis date.  Results are
    ordered newest first by generated_at (falling back to created_at).

    ``triage_tier`` optionally filters to one tier (``pre_clear`` | ``review``
    | ``escalate``). Filtering by tier — not the score band derived from
    ``overall_score`` — matters because a critical signal (e.g. a sanctions
    hit) can force ``escalate`` at a low score (ARCHITECTURE § triage).

    This is a thin list — it does NOT return evidence, mismatches, or
    full scores.  Use GET /reports/{run_id} for the full report.
    """
    reports = db.query(Report).order_by(Report.created_at.desc()).all()

    items: list[ReportListItemSchema] = []
    for report in reports:
        # Overall score / tier from summary JSON
        summary = report.summary or {}
        scores_data = summary.get("scores") or {}
        overall_score = scores_data.get("overall_score")
        report_triage_tier = scores_data.get("triage_tier")

        if triage_tier is not None and report_triage_tier != triage_tier:
            continue

        # Resolve company info from submission via verification_run
        run = db.get(VerificationRun, report.verification_run_id)
        company_name = ""
        domain = ""
        if run is not None:
            sub = db.get(Submission, run.submission_id)
            if sub is not None:
                company_name = sub.company_name
                domain = sub.domain

        # Review status — None if no review row exists
        review = (
            db.query(Review)
            .filter(Review.verification_run_id == report.verification_run_id)
            .first()
        )
        review_status = review.status if review is not None else None

        generated_at = report.generated_at.isoformat() if report.generated_at else None

        items.append(
            ReportListItemSchema(
                run_id=report.verification_run_id,
                report_id=report.id,
                company_name=company_name,
                domain=domain,
                status=report.status,
                overall_score=overall_score,
                triage_tier=report_triage_tier,
                review_status=review_status,
                generated_at=generated_at,
            )
        )

    return ReportListResponse(items=items, total=len(items))


@router.get(
    "/{run_id}/export",
    response_model=ReportResponse,
    summary="Machine-readable report export for integrating systems (P2-T11)",
)
def export_report(
    run_id: str,
    db: Session = Depends(_get_db),
    principal: Principal = Depends(get_principal),
) -> ReportResponse:
    """Return the full normalized report for automated extraction.

    Identical payload to GET /reports/{run_id} but signals intent for
    programmatic consumption by integrating systems (PRD § API Requirements).
    Wraps the same _serialize_report() helper.

    Requires an authenticated principal — an integrating system (X-API-Key) or
    an operator (Bearer token).  Every pull is audited and attributed to the
    caller (P2-T12).

    Returns 404 if the run or report does not exist.
    """
    run = db.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification run {run_id!r} not found.",
        )

    report = db.query(Report).filter(Report.verification_run_id == run_id).first()
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Report for run {run_id!r} has not been assembled yet. "
                "The pipeline may still be in early stages."
            ),
        )

    # Attribute the report pull to the calling principal (ARCHITECTURE § 5).
    record_event(
        db=db,
        event_type="report.exported",
        operator_id=principal.operator_id,
        api_client_id=principal.api_client_id,
        verification_run_id=run_id,
        payload={"by": principal.kind, "principal": principal.name},
        description=(
            f"Report for run {run_id!r} exported by "
            f"{principal.kind} {principal.name!r}."
        ),
    )

    viewer = resolve_viewer(principal, db)
    return _serialize_report(
        report, _review_summary(db, report.verification_run_id), run, viewer=viewer
    )


@router.get(
    "/{run_id}",
    response_model=ReportResponse,
    summary="Retrieve the report for a verification run",
)
def get_report(
    run_id: str,
    db: Session = Depends(_get_db),
    principal: Principal = Depends(get_principal),
) -> ReportResponse:
    """Return the report for the given verification run ID.

    - Completed run: returns full report with scores, evidence, mismatches, sources.
    - In-progress run: returns partial report; section_statuses indicates which
      sections are pending vs. complete.
    - Unknown run_id: returns 404.

    The response is redacted per the caller's role (ADR-0002 §2, via
    app.auth.pii). Viewing a report that contains PII is audited.
    """
    # Verify the run exists first (for a clear 404 on unknown run vs. no-report-yet)
    run = db.get(VerificationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Verification run {run_id!r} not found.",
        )

    report = db.query(Report).filter(Report.verification_run_id == run_id).first()
    if report is None:
        # Run exists but report not yet assembled (still pending / very early pipeline)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Report for run {run_id!r} has not been assembled yet. "
                "The pipeline may still be in early stages."
            ),
        )

    if report_contains_pii(report.summary or {}):
        record_event(
            db=db,
            event_type="report.viewed",
            operator_id=principal.operator_id,
            api_client_id=principal.api_client_id,
            verification_run_id=run_id,
            payload={"by": principal.kind, "principal": principal.name},
            description=(
                f"Report for run {run_id!r} (contains PII) viewed by "
                f"{principal.kind} {principal.name!r}."
            ),
        )

    viewer = resolve_viewer(principal, db)
    return _serialize_report(
        report, _review_summary(db, report.verification_run_id), run, viewer=viewer
    )
