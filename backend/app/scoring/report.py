"""Report assembly (P1-T7, extended P2-T7).

Assembles or updates a queryable Report row for a verification run.  The
report is readable at any point during the run (partial results — each
section carries its own status).

Report sections and their source:
  scores          — from RiskAssessment (if scored)
  evidence        — raw evidence rows (one summary entry per row)
  mismatches      — FieldComparison rows with status "mismatch" or "unverified"
  sources         — distinct sources used, with per-source evidence counts
  triage          — TriageResult dict (P2-T7): tier, reason, critical_signals
  explainability  — ExplainabilityPayload dict (P2-T7): per-layer signals +
                    evidence attribution + source coverage

Section statuses:
  pending     — no data yet for this section
  complete    — data present and stage finished
  unavailable — stage ran but source was unavailable

Usage:
    from app.scoring.report import assemble_report
    assemble_report(run_id, db)

The pipeline StoreReportStage calls this after ScoringStage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _build_section_statuses(
    source_availability: dict | None,
    has_assessment: bool,
) -> dict[str, str]:
    """Derive per-section status from run's source_availability and scoring.

    Sections:
      scores         — complete if RiskAssessment exists, else pending
      evidence       — follows query_registries + analyze_domain stages
      mismatches     — follows consistency_checks stage
      sources        — follows evidence (same dependency)
    """
    avail = source_availability or {}

    def _stage_status(stage_name: str) -> str:
        return avail.get(stage_name, "pending")

    # Evidence section: complete if either registry or domain stage ran
    reg_status = _stage_status("query_registries")
    domain_status = _stage_status("analyze_domain")
    if reg_status == "complete" or domain_status == "complete":
        evidence_section = "complete"
    elif reg_status == "unavailable" and domain_status == "unavailable":
        evidence_section = "unavailable"
    else:
        evidence_section = "pending"

    consistency_status = _stage_status("consistency_checks")
    mismatches_section = consistency_status if consistency_status else "pending"

    return {
        "scores": "complete" if has_assessment else "pending",
        "evidence": evidence_section,
        "mismatches": mismatches_section,
        "sources": evidence_section,  # sources depend on evidence
    }


# Pipeline stage name → (evidence source name, tier) for adapter stages, so an
# unavailable stage can be listed under the same name its evidence would carry.
_STAGE_SOURCES: dict[str, tuple[str, int]] = {
    "query_registries": ("opencorporates", 1),
    "sanctions_screening": ("sanctions", 1),
    "analyze_domain": ("domain", 2),
    "enrich_network_ip": ("ipinfo", 2),
    "web_evidence": ("web", 3),
}


def _build_summary(
    evidence_rows: list,
    field_comparisons: list,
    assessment,
    source_availability: dict | None = None,
) -> dict:
    """Build the denormalized summary dict stored on the Report row.

    This is what the API serializes for consumers.  Structure (P2-T7 extended):
    {
      "scores": {...} | None,
      "evidence": [...],
      "mismatches": [...],
      "sources": {...},
      "triage": {...} | None,          # TriageResult (P2-T7)
      "explainability": {...} | None   # ExplainabilityPayload (P2-T7)
    }
    """
    # Scores section
    if assessment is not None:
        scores = {
            "overall_score": assessment.overall_score,
            "entity_score": assessment.entity_score,
            "infrastructure_score": assessment.infrastructure_score,
            "representation_score": assessment.representation_score,
            "risk_score": assessment.risk_score,
            "triage_tier": assessment.triage_tier,
            "contributing_signals": assessment.contributing_signals or [],
        }
    else:
        scores = None

    # Evidence section — summary per evidence row
    evidence_summary = [
        {
            "id": ev.id,
            "source": ev.source,
            "tier": ev.tier,
            "field": ev.field,
            "raw_value": ev.raw_value,
            "normalized_value": ev.normalized_value,
            "confidence": ev.confidence,
            "attribution": ev.attribution,
            "fetched_at": ev.fetched_at.isoformat() if ev.fetched_at else None,
        }
        for ev in evidence_rows
    ]

    # Mismatches/comparisons section
    mismatches = [
        {
            "id": fc.id,
            "field_name": fc.field_name,
            "submitted_value": fc.submitted_value,
            "discovered_value": fc.discovered_value,
            "match_status": fc.match_status,
            "evidence_id": fc.evidence_id,
        }
        for fc in field_comparisons
    ]

    # Sources section — distinct sources with counts
    sources: dict[str, dict] = {}
    for ev in evidence_rows:
        if ev.source not in sources:
            sources[ev.source] = {
                "source": ev.source,
                "tier": ev.tier,
                "evidence_count": 0,
                "attribution": ev.attribution,
            }
        sources[ev.source]["evidence_count"] += 1
    for src in sources.values():
        src["status"] = "available"
    # Adapter stages whose source was down produced no evidence; list them so
    # the operator sees the gap instead of a silently shorter source list.
    for stage_name, status in (source_availability or {}).items():
        mapped = _STAGE_SOURCES.get(stage_name)
        if status == "unavailable" and mapped and mapped[0] not in sources:
            sources[mapped[0]] = {
                "source": mapped[0],
                "tier": mapped[1],
                "evidence_count": 0,
                "attribution": None,
                "status": "unavailable",
            }

    # Triage section (P2-T7) — TriageResult derived from assessment signals
    triage: dict | None = None
    explainability: dict | None = None

    if assessment is not None:
        # Re-derive triage + explainability from the persisted assessment data.
        # We reconstruct lightweight objects rather than re-running the full engine.
        triage = _derive_triage_from_assessment(assessment)
        explainability = _build_explainability_from_assessment(
            assessment, evidence_rows, field_comparisons
        )

    return {
        "scores": scores,
        "evidence": evidence_summary,
        "mismatches": mismatches,
        "sources": list(sources.values()),
        "triage": triage,
        "explainability": explainability,
    }


def _derive_triage_from_assessment(assessment) -> dict:
    """Derive a TriageResult dict from a persisted RiskAssessment.

    Reconstructs a ScoringResult-like object from the persisted JSON fields
    and delegates to triage.derive_triage().
    """
    from app.scoring.engine import ScoringResult, Signal  # noqa: PLC0415
    from app.scoring.triage import derive_triage  # noqa: PLC0415

    signals = [
        Signal(
            name=s.get("name", ""),
            layer=s.get("layer", "entity"),
            direction=s.get("direction", "elevated"),
            weight=s.get("weight", 0.0),
            description=s.get("description", ""),
            evidence_ids=s.get("evidence_ids", []),
        )
        for s in (assessment.contributing_signals or [])
    ]

    pseudo_result = ScoringResult(
        entity_score=assessment.entity_score or 50.0,
        infrastructure_score=assessment.infrastructure_score or 50.0,
        representation_score=assessment.representation_score or 50.0,
        risk_score=assessment.risk_score or 50.0,
        overall_score=assessment.overall_score or 50.0,
        triage_tier=assessment.triage_tier or "review",
        contributing_signals=signals,
        confidence=0.0,  # confidence not stored; only tier matters here
    )

    return derive_triage(pseudo_result).to_dict()


def _build_explainability_from_assessment(
    assessment, evidence_rows: list, field_comparisons: list
) -> dict:
    """Build an ExplainabilityPayload dict from a persisted RiskAssessment.

    Reconstructs Signal objects from the persisted JSON and delegates to
    explain.build_explainability().
    """
    from app.scoring.engine import ScoringResult, Signal  # noqa: PLC0415
    from app.scoring.explain import build_explainability  # noqa: PLC0415

    signals = [
        Signal(
            name=s.get("name", ""),
            layer=s.get("layer", "entity"),
            direction=s.get("direction", "elevated"),
            weight=s.get("weight", 0.0),
            description=s.get("description", ""),
            evidence_ids=s.get("evidence_ids", []),
        )
        for s in (assessment.contributing_signals or [])
    ]

    # Estimate confidence from evidence tier coverage (same formula as engine)
    tiers_present = {e.tier for e in evidence_rows if e.tier in (1, 2, 3)}
    confidence = len(tiers_present) / 3.0 if tiers_present else 0.0

    pseudo_result = ScoringResult(
        entity_score=assessment.entity_score or 50.0,
        infrastructure_score=assessment.infrastructure_score or 50.0,
        representation_score=assessment.representation_score or 50.0,
        risk_score=assessment.risk_score or 50.0,
        overall_score=assessment.overall_score or 50.0,
        triage_tier=assessment.triage_tier or "review",
        contributing_signals=signals,
        confidence=confidence,
    )

    return build_explainability(
        pseudo_result, evidence_rows, field_comparisons
    ).to_dict()


def assemble_report(run_id: str, db: "Session") -> None:
    """Create or update the Report row for a verification run.

    Called by StoreReportStage.  Creates a new Report if none exists for
    this run; updates if one already exists (idempotent).

    Partial results: sections are marked pending/complete based on which
    pipeline stages have completed (VerificationRun.source_availability).
    """
    from app.models.evidence import Evidence  # noqa: PLC0415
    from app.models.field_comparison import FieldComparison  # noqa: PLC0415
    from app.models.report import Report  # noqa: PLC0415
    from app.models.risk_assessment import RiskAssessment  # noqa: PLC0415
    from app.models.verification_run import VerificationRun  # noqa: PLC0415

    run = db.get(VerificationRun, run_id)
    if run is None:
        logger.error("assemble_report: run %s not found", run_id)
        return

    evidence_rows = (
        db.query(Evidence).filter(Evidence.verification_run_id == run_id).all()
    )
    field_comparisons = (
        db.query(FieldComparison)
        .filter(FieldComparison.verification_run_id == run_id)
        .all()
    )
    assessment = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.verification_run_id == run_id)
        .order_by(RiskAssessment.created_at.desc())
        .first()
    )

    section_statuses = _build_section_statuses(
        run.source_availability, has_assessment=assessment is not None
    )
    summary = _build_summary(
        evidence_rows, field_comparisons, assessment, run.source_availability
    )

    # Determine overall report status
    if all(v == "complete" for v in section_statuses.values()):
        report_status = "complete"
    elif all(v in ("complete", "unavailable") for v in section_statuses.values()):
        report_status = "complete"
    elif any(v == "complete" for v in section_statuses.values()):
        report_status = "partial"
    else:
        report_status = "pending"

    # Create or update
    existing = db.query(Report).filter(Report.verification_run_id == run_id).first()

    now = datetime.now(tz=timezone.utc)

    if existing is None:
        report = Report(
            verification_run_id=run_id,
            risk_assessment_id=assessment.id if assessment else None,
            status=report_status,
            section_statuses=section_statuses,
            summary=summary,
            generated_at=now if report_status == "complete" else None,
        )
        db.add(report)
    else:
        existing.risk_assessment_id = assessment.id if assessment else None
        existing.status = report_status
        existing.section_statuses = section_statuses
        existing.summary = summary
        existing.updated_at = now  # type: ignore[assignment]
        if report_status == "complete" and existing.generated_at is None:
            existing.generated_at = now

    db.commit()


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------


class StoreReportStage:
    """Pipeline stage 9: store/update the report (P1-T7).

    Calls assemble_report() to create/update the Report row.  Idempotent —
    can be called multiple times; always reflects the latest run state.
    """

    name = "store_report"

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        try:
            assemble_report(run_id, db)
        except Exception as exc:
            logger.warning("StoreReportStage error: %s", exc)
            return {**context, "report": {"status": "error", "message": str(exc)}}

        return {**context, "report": {"status": "complete"}}
