"""Pydantic schemas for the report retrieval endpoint (P1-T8).

Designed for partial-result visibility: each section carries its own status
so in-progress runs are readable mid-pipeline.

Section status values:
  pending     — pipeline stage has not yet run
  complete    — stage ran successfully; data is present
  unavailable — stage ran but source was unavailable / failed
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Nested schemas
# ---------------------------------------------------------------------------


class ScoresSchema(BaseModel):
    """Four-layer confidence breakdown + overall score."""

    overall_score: float | None = None
    entity_score: float | None = None
    infrastructure_score: float | None = None
    representation_score: float | None = None
    risk_score: float | None = None
    triage_tier: str | None = None
    contributing_signals: list[dict[str, Any]] = []


class EvidenceItemSchema(BaseModel):
    """One evidence finding from one source."""

    id: str
    source: str
    tier: int
    field: str | None = None
    raw_value: str | None = None
    normalized_value: str | None = None
    confidence: float | None = None
    attribution: dict[str, Any] | None = None
    fetched_at: str | None = None


class MismatchItemSchema(BaseModel):
    """One field comparison result."""

    id: str
    field_name: str
    submitted_value: str | None = None
    discovered_value: str | None = None
    match_status: str  # "match" | "mismatch" | "unverified"
    evidence_id: str | None = None


class SourceSummarySchema(BaseModel):
    """Distinct source used, with evidence count."""

    source: str
    tier: int
    evidence_count: int
    attribution: dict[str, Any] | None = None
    # "available" or "unavailable" (source down: timeout / error / rate limit).
    status: str = "available"


# ---------------------------------------------------------------------------
# Section status wrapper
# ---------------------------------------------------------------------------


class SectionStatuses(BaseModel):
    """Per-section pipeline status.

    Each value is "pending" | "complete" | "unavailable".
    Consumers should check section status before reading section data.
    """

    scores: str = "pending"
    evidence: str = "pending"
    mismatches: str = "pending"
    sources: str = "pending"


# ---------------------------------------------------------------------------
# Report response
# ---------------------------------------------------------------------------


class ReviewSummarySchema(BaseModel):
    """The human decision on a run (latest Review row), if any."""

    status: str
    notes: str | None = None
    reviewer_name: str | None = None
    decided_at: str | None = None


class ReportResponse(BaseModel):
    """Full or partial report returned by GET /reports/{run_id}.

    A partial report (status="partial") is returned when the pipeline is
    still running.  Section data for completed sections is already present;
    pending sections have empty lists / null fields.

    Status values: "pending" | "partial" | "complete" | "failed"
    """

    run_id: str
    report_id: str
    status: str  # overall report status
    section_statuses: SectionStatuses

    # Sections — populated according to section_statuses
    scores: ScoresSchema | None = None
    evidence: list[EvidenceItemSchema] = []
    mismatches: list[MismatchItemSchema] = []
    sources: list[SourceSummarySchema] = []

    generated_at: str | None = None

    # Null until an operator reviews the run.
    review: ReviewSummarySchema | None = None


# ---------------------------------------------------------------------------
# List endpoint schema (GET /reports/)
# ---------------------------------------------------------------------------


class ReportListItemSchema(BaseModel):
    """Thin summary row for the dashboard list (GET /reports/).

    Contains enough to render the operator queue: company name, run status,
    overall risk score, review status, and analysis date.
    """

    run_id: str
    report_id: str
    company_name: str
    domain: str
    status: str  # report status: "pending" | "partial" | "complete" | "failed"
    overall_score: float | None = None
    review_status: str | None = None  # None = not yet reviewed
    generated_at: str | None = None


class ReportListResponse(BaseModel):
    """Response for GET /reports/."""

    items: list[ReportListItemSchema]
    total: int
