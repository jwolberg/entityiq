"""Risk scoring engine (P1-T7 foundation, extended in P2-T6).

Produces a deterministic, evidence-based risk assessment for a verification run.
Outputs:
  - overall_score: 0–100 (higher = more risk)
  - entity_score, infrastructure_score, representation_score, risk_score (each 0–100)
  - triage_tier: "pre_clear" | "review" | "escalate"
  - contributing_signals: list of signal dicts, each referencing evidence_id(s)
  - Persists a RiskAssessment row with risk_assessment_evidence links

INVARIANT: this engine is ADVISORY ONLY.  It produces NO approve/reject decision.
A human operator must decide.  See PRD § Non-Goals and ARCHITECTURE § 3.

Scoring philosophy (deterministic, explainable):
  - Scores are derived exclusively from evidence already persisted for the run.
  - Each contributing signal MUST reference ≥1 evidence row (no orphan signals).
  - Missing / unavailable sources → reduced confidence, NOT defaulted to low risk.
  - Triage tiers are thresholds on overall_score (configurable constants).
  - No ML, no black-box — every signal and its weight is explicit here.

Four layers (PRD § Core Verification Philosophy):
  Layer 1 — Entity legitimacy        (entity_score)
  Layer 2 — Infrastructure legitimacy (infrastructure_score)
  Layer 3 — Representation confidence (representation_score)
  Layer 4 — Fraud/staging risk        (risk_score)

Overall score is a weighted average of the four layer scores.  Layer weights
can be tuned later; v1 uses equal weights (0.25 each).

P2-T6 changes:
  - Full signal catalog moved to ``app.scoring.signals``.
  - All four layer functions delegate to signals.py for the PRD-complete catalog.
  - ScoringEngine.score() accepts an optional ``db`` and ``run_id`` to enable
    cross-submission IP/ASN reuse detection (deferred from P2-T2).
  - Layer-score formula unchanged (_layer_score_from_signals).
  - Confidence calculation unchanged (tier coverage fraction).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Lazy import guard: signals.py imports Signal from this module, so we import
# the signal catalog functions only inside the layer helpers (not at module top)
# to avoid any circular-import risk.  The actual functions are fast (no I/O).

# ---------------------------------------------------------------------------
# Triage thresholds (overall_score ∈ [0, 100]; higher = more risky)
# ---------------------------------------------------------------------------

TRIAGE_PRE_CLEAR_MAX = 30  # overall_score ≤ 30 → pre_clear
TRIAGE_ESCALATE_MIN = 70  # overall_score ≥ 70 → escalate
# 31–69 → "review"

# Signals that alone force "escalate" regardless of overall_score.  Lives here
# (not triage.py) so the tier PERSISTED by the engine and the tier shown in the
# report's triage section can never disagree.
CRITICAL_ESCALATION_SIGNALS: frozenset[str] = frozenset(
    {
        "sanctions_hit",
        "sanctions_hit_fraud_flag",
        "ip_asn_reuse_high",
    }
)

# ---------------------------------------------------------------------------
# Layer weights for overall_score (v1: equal)
# ---------------------------------------------------------------------------

_LAYER_WEIGHTS = {
    "entity": 0.25,
    "infrastructure": 0.25,
    "representation": 0.25,
    "risk": 0.25,
}

# ---------------------------------------------------------------------------
# Signal data structures
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    """One contributing signal in the risk assessment.

    Every signal MUST reference at least one evidence_id so it is attributable.
    """

    name: str  # e.g. "registry_confirmed", "recently_registered_domain"
    layer: str  # "entity" | "infrastructure" | "representation" | "risk"
    direction: str  # "trust" (reduces risk) | "elevated" (increases risk)
    weight: float  # 0.0–1.0 within-layer contribution
    description: str
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "layer": self.layer,
            "direction": self.direction,
            "weight": self.weight,
            "description": self.description,
            "evidence_ids": self.evidence_ids,
        }


@dataclass
class ScoringResult:
    """Output of the scoring engine — not yet persisted."""

    entity_score: float
    infrastructure_score: float
    representation_score: float
    risk_score: float
    overall_score: float
    triage_tier: str
    contributing_signals: list[Signal]
    confidence: float  # 0.0–1.0; reduced when sources are unavailable


# ---------------------------------------------------------------------------
# Layer scoring helpers — delegate to the full signal catalog in signals.py
# ---------------------------------------------------------------------------


def _score_entity_legitimacy(evidence_rows: list) -> tuple[float, list[Signal]]:
    """Layer 1: Does this business appear to exist?

    Delegates to signals.entity_legitimacy_signals() for the full PRD catalog.
    Returns (score 0–100, signals).  Score 0 = low risk (entity confirmed).
    """
    from app.scoring.signals import entity_legitimacy_signals  # noqa: PLC0415

    signals = entity_legitimacy_signals(evidence_rows)

    # Special case: no Tier-1 evidence at all → return elevated uncertainty score
    if len(signals) == 1 and signals[0].name == "no_registry_evidence":
        return 60.0, signals

    score = _layer_score_from_signals(signals)
    return score, signals


def _score_infrastructure_legitimacy(evidence_rows: list) -> tuple[float, list[Signal]]:
    """Layer 2: Does the org control the claimed domain and communications infra?

    Delegates to signals.infrastructure_legitimacy_signals() for the full catalog.
    """
    from app.scoring.signals import infrastructure_legitimacy_signals  # noqa: PLC0415

    signals = infrastructure_legitimacy_signals(evidence_rows)

    # Special case: no Tier-2 evidence → return neutral-uncertain score
    if len(signals) == 1 and signals[0].name == "infrastructure_data_unavailable":
        return 40.0, signals

    score = _layer_score_from_signals(signals)
    return score, signals


def _score_representation_confidence(
    evidence_rows: list,
    field_comparisons: list,
) -> tuple[float, list[Signal]]:
    """Layer 3: Does the requester plausibly represent the organization?

    Delegates to signals.representation_confidence_signals() for the full catalog.
    """
    from app.scoring.signals import representation_confidence_signals  # noqa: PLC0415

    signals = representation_confidence_signals(evidence_rows, field_comparisons)

    # Special case: no field comparisons → reduced confidence score
    if len(signals) == 1 and signals[0].name == "no_field_comparisons":
        return 35.0, signals

    # Special case: all fields unverified
    if len(signals) == 1 and signals[0].name == "all_fields_unverified":
        return 45.0, signals

    score = _layer_score_from_signals(signals)
    return score, signals


def _score_fraud_staging_risk(
    evidence_rows: list,
    run_id: str | None = None,
    db: "Session | None" = None,
) -> tuple[float, list[Signal]]:
    """Layer 4: Are there signs of fraud, staging, impersonation, or sanctions evasion?

    Delegates to signals.fraud_staging_risk_signals() for the full PRD catalog.
    When db and run_id are provided, cross-submission IP/ASN reuse is detected.
    """
    from app.scoring.signals import fraud_staging_risk_signals  # noqa: PLC0415

    signals = fraud_staging_risk_signals(evidence_rows, run_id=run_id, db=db)

    # Special case: no infra/web data at all → return uncertain score
    if len(signals) == 1 and signals[0].name == "fraud_signals_unavailable":
        return 35.0, signals

    score = _layer_score_from_signals(signals)
    return score, signals


# ---------------------------------------------------------------------------
# Layer score computation
# ---------------------------------------------------------------------------


def _layer_score_from_signals(signals: list[Signal]) -> float:
    """Compute a 0–100 risk score from a list of signals.

    Algorithm:
      - Start at 50 (neutral / no data).
      - Trust signals DECREASE the score (good — less risk).
      - Elevated signals INCREASE the score (bad — more risk).
      - Each signal's contribution is weight × 40 (max swing of 40 per signal).
      - Score is clamped to [0, 100].

    This gives a simple, explainable, linear model.
    """
    if not signals:
        return 50.0

    score = 50.0
    for sig in signals:
        delta = sig.weight * 40.0
        if sig.direction == "trust":
            score -= delta
        else:
            score += delta

    return max(0.0, min(100.0, score))


def _compute_overall_score(layer_scores: dict[str, float]) -> float:
    """Weighted average of layer scores."""
    total_weight = sum(_LAYER_WEIGHTS.values())
    weighted_sum = sum(
        layer_scores[layer] * weight for layer, weight in _LAYER_WEIGHTS.items()
    )
    return max(0.0, min(100.0, weighted_sum / total_weight))


def _triage_tier(overall_score: float, signal_names: set[str] | None = None) -> str:
    """Map overall score to a triage tier; critical signals force escalate.

    pre_clear: ≤ 30 — low risk, operator may expedite review
    escalate:  ≥ 70 — high risk, requires close scrutiny
    review:    31–69 — standard review

    INVARIANT: this is a TRIAGE SIGNAL, not an approval decision.
    The human operator decides; the tier is a queue-management aid.
    """
    if signal_names and signal_names & CRITICAL_ESCALATION_SIGNALS:
        return "escalate"
    if overall_score <= TRIAGE_PRE_CLEAR_MAX:
        return "pre_clear"
    if overall_score >= TRIAGE_ESCALATE_MIN:
        return "escalate"
    return "review"


def _compute_confidence(evidence_rows: list) -> float:
    """Compute source coverage confidence: fraction of tiers with evidence.

    0.0 → no evidence at all
    1.0 → evidence from all three tiers

    Reduced confidence reflects missing sources, not increased risk.
    """
    tiers_present = {e.tier for e in evidence_rows if e.tier in (1, 2, 3)}
    if not tiers_present:
        return 0.0
    return len(tiers_present) / 3.0


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------


class ScoringEngine:
    """Deterministic risk scoring engine (P1-T7 base, P2-T6 full catalog).

    Usage:
        result = ScoringEngine().score(evidence_rows, field_comparisons)
        # With cross-submission reuse detection:
        result = ScoringEngine().score(evidence_rows, field_comparisons,
                                       run_id=run_id, db=db_session)
    """

    def score(
        self,
        evidence_rows: list,
        field_comparisons: list,
        run_id: str | None = None,
        db: "Session | None" = None,
    ) -> ScoringResult:
        """Compute risk scores from evidence and field comparison rows.

        Args:
            evidence_rows:    All Evidence ORM rows for this run.
            field_comparisons: All FieldComparison ORM rows for this run.
            run_id:           Optional — the current verification run ID.
                              Required for cross-submission IP/ASN reuse detection.
            db:               Optional — open SQLAlchemy session.
                              Required for cross-submission IP/ASN reuse detection.

        Returns:
            ScoringResult — see dataclass definition above.

        INVARIANT: never produces an approve/reject decision.
        """
        entity_score, entity_signals = _score_entity_legitimacy(evidence_rows)
        infra_score, infra_signals = _score_infrastructure_legitimacy(evidence_rows)
        rep_score, rep_signals = _score_representation_confidence(
            evidence_rows, field_comparisons
        )
        risk_score, risk_signals = _score_fraud_staging_risk(
            evidence_rows, run_id=run_id, db=db
        )

        layer_scores = {
            "entity": entity_score,
            "infrastructure": infra_score,
            "representation": rep_score,
            "risk": risk_score,
        }
        overall = _compute_overall_score(layer_scores)
        confidence = _compute_confidence(evidence_rows)

        all_signals = entity_signals + infra_signals + rep_signals + risk_signals
        tier = _triage_tier(overall, {s.name for s in all_signals})

        return ScoringResult(
            entity_score=entity_score,
            infrastructure_score=infra_score,
            representation_score=rep_score,
            risk_score=risk_score,
            overall_score=overall,
            triage_tier=tier,
            contributing_signals=all_signals,
            confidence=confidence,
        )


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------


class ScoringStage:
    """Pipeline stage 8: generate risk assessment (P1-T7).

    Reads evidence and field comparisons persisted by prior stages, calls
    ScoringEngine, and persists a RiskAssessment row (with evidence links).
    """

    name = "scoring"

    def __init__(self, engine: ScoringEngine | None = None) -> None:
        self._engine = engine if engine is not None else ScoringEngine()

    def run(self, run_id: str, db: "Session", context: dict) -> dict:
        from app.models.evidence import Evidence  # noqa: PLC0415
        from app.models.field_comparison import FieldComparison  # noqa: PLC0415
        from app.models.risk_assessment import RiskAssessment  # noqa: PLC0415

        evidence_rows = (
            db.query(Evidence).filter(Evidence.verification_run_id == run_id).all()
        )
        field_comparisons = (
            db.query(FieldComparison)
            .filter(FieldComparison.verification_run_id == run_id)
            .all()
        )

        result = self._engine.score(
            evidence_rows, field_comparisons, run_id=run_id, db=db
        )

        # Collect all evidence rows referenced by signals
        referenced_ids: set[str] = set()
        for sig in result.contributing_signals:
            referenced_ids.update(sig.evidence_ids)

        # Build evidence lookup for linking
        ev_by_id = {e.id: e for e in evidence_rows}

        assessment = RiskAssessment(
            verification_run_id=run_id,
            entity_score=result.entity_score,
            infrastructure_score=result.infrastructure_score,
            representation_score=result.representation_score,
            risk_score=result.risk_score,
            overall_score=result.overall_score,
            triage_tier=result.triage_tier,
            contributing_signals=[sig.to_dict() for sig in result.contributing_signals],
        )

        # Link referenced evidence rows via association table
        for ev_id in referenced_ids:
            ev = ev_by_id.get(ev_id)
            if ev is not None:
                assessment.evidence_items.append(ev)

        db.add(assessment)
        db.commit()

        return {
            **context,
            "scoring": {
                "status": "complete",
                "overall_score": result.overall_score,
                "triage_tier": result.triage_tier,
                "assessment_id": assessment.id,
                "confidence": result.confidence,
            },
        }
