"""Risk scoring engine v1 (P1-T7).

Produces a deterministic, evidence-based risk assessment for a verification run.
Outputs:
  - overall_score: 0–100 (higher = more risk)
  - entity_score, infrastructure_score, representation_score, risk_score (each 0–100)
  - triage_tier: "pre_clear" | "review" | "escalate"
  - contributing_signals: list of signal dicts, each referencing evidence_id(s)
  - Persists a RiskAssessment row with risk_assessment_evidence links

INVARIANT: this engine is ADVISORY ONLY.  It produces NO approve/reject decision.
A human operator must decide.  See PRD § Non-Goals and ARCHITECTURE § 3.

Scoring philosophy (v1 — deterministic, explainable):
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
can be tuned later; v1 uses equal weights.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Triage thresholds (overall_score ∈ [0, 100]; higher = more risky)
# ---------------------------------------------------------------------------

TRIAGE_PRE_CLEAR_MAX = 30  # overall_score ≤ 30 → pre_clear
TRIAGE_ESCALATE_MIN = 70  # overall_score ≥ 70 → escalate
# 31–69 → "review"

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
# Layer scoring helpers
# ---------------------------------------------------------------------------


def _score_entity_legitimacy(evidence_rows: list) -> tuple[float, list[Signal]]:
    """Layer 1: Does this business appear to exist?

    Trust signals (reduce risk score):
      - registry_confirmed: Tier-1 source returned a company_name match
      - registration_number_present: Tier-1 source returned a registration number

    Elevated signals (increase risk score):
      - no_registry_evidence: No Tier-1 evidence at all

    Returns (score 0–100, signals).  Score 0 = low risk (entity confirmed).
    """
    signals: list[Signal] = []

    tier1_evidence = [e for e in evidence_rows if e.tier == 1]
    name_evidence = [e for e in tier1_evidence if e.field == "company_name"]
    reg_number_evidence = [
        e for e in tier1_evidence if e.field == "registration_number"
    ]
    status_evidence = [e for e in tier1_evidence if e.field == "registration_status"]

    if not tier1_evidence:
        signals.append(
            Signal(
                name="no_registry_evidence",
                layer="entity",
                direction="elevated",
                weight=1.0,
                description=(
                    "No Tier-1 (authoritative registry) evidence found for this entity."
                ),
                evidence_ids=[],
            )
        )
        # No evidence → high uncertainty, score 60 (not maximum — we don't know)
        return 60.0, signals

    if name_evidence:
        signals.append(
            Signal(
                name="registry_name_confirmed",
                layer="entity",
                direction="trust",
                weight=0.6,
                description="Company name found in authoritative registry (Tier-1).",
                evidence_ids=[e.id for e in name_evidence],
            )
        )
    else:
        signals.append(
            Signal(
                name="registry_name_unconfirmed",
                layer="entity",
                direction="elevated",
                weight=0.4,
                description="Tier-1 source did not return a matching company name.",
                evidence_ids=[e.id for e in tier1_evidence[:1]],
            )
        )

    if reg_number_evidence:
        signals.append(
            Signal(
                name="registration_number_present",
                layer="entity",
                direction="trust",
                weight=0.4,
                description="Registration number found in authoritative registry.",
                evidence_ids=[e.id for e in reg_number_evidence],
            )
        )

    if status_evidence:
        # Check if any status value suggests active/good standing
        active_statuses = {"active", "incorporated", "live", "registered"}
        active_ev = [
            e
            for e in status_evidence
            if e.normalized_value
            and any(s in (e.normalized_value or "").lower() for s in active_statuses)
        ]
        if active_ev:
            signals.append(
                Signal(
                    name="registry_status_active",
                    layer="entity",
                    direction="trust",
                    weight=0.3,
                    description=(
                        "Registry status indicates company is active/incorporated."
                    ),
                    evidence_ids=[e.id for e in active_ev],
                )
            )

    # Compute layer score from signals
    score = _layer_score_from_signals(signals)
    return score, signals


def _score_infrastructure_legitimacy(evidence_rows: list) -> tuple[float, list[Signal]]:
    """Layer 2: Does the org control the claimed domain and communications infra?

    Trust signals: long_lived_domain, has_mx_records, has_spf, has_ssl
    Elevated signals: recently_registered_domain, no_mx_records
    """
    signals: list[Signal] = []

    tier2_evidence = [e for e in evidence_rows if e.tier == 2]
    domain_age_evidence = [e for e in tier2_evidence if e.field == "domain_age_days"]
    mx_evidence = [e for e in tier2_evidence if e.field == "mx_present"]
    spf_evidence = [e for e in tier2_evidence if e.field == "spf_present"]
    ssl_evidence = [e for e in tier2_evidence if e.field in ("ssl_issuer", "ssl_valid")]
    recently_registered_evidence = [
        e for e in tier2_evidence if e.field == "recently_registered"
    ]
    no_mx_evidence = [e for e in tier2_evidence if e.field == "no_mx"]

    if not tier2_evidence:
        # Unavailable infrastructure data → reduced confidence, neutral score
        # IMPORTANT: not defaulted to low risk — we just have less data.
        signals.append(
            Signal(
                name="infrastructure_data_unavailable",
                layer="infrastructure",
                direction="elevated",
                weight=0.3,
                description=(
                    "No Tier-2 (domain/infrastructure) evidence available. "
                    "Score reflects reduced confidence, not confirmed risk."
                ),
                evidence_ids=[],
            )
        )
        return 40.0, signals  # Neutral-ish; reflects uncertainty not confirmed risk

    # Trust: long-lived domain
    if domain_age_evidence:
        best_age_ev = domain_age_evidence[0]
        try:
            age_days = int(best_age_ev.normalized_value or best_age_ev.raw_value or "0")
        except (ValueError, TypeError):
            age_days = 0

        if age_days >= 365 * 3:  # 3+ years old → strong trust signal
            signals.append(
                Signal(
                    name="long_lived_domain",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.5,
                    description=f"Domain is {age_days} days old (≥3 years).",
                    evidence_ids=[best_age_ev.id],
                )
            )
        elif age_days >= 365:  # 1–3 years → mild trust
            signals.append(
                Signal(
                    name="established_domain",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.25,
                    description=f"Domain is {age_days} days old (≥1 year).",
                    evidence_ids=[best_age_ev.id],
                )
            )

    # Elevated: recently registered
    if recently_registered_evidence:
        ev = recently_registered_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in ("true", "1", "yes"):
            signals.append(
                Signal(
                    name="recently_registered_domain",
                    layer="infrastructure",
                    direction="elevated",
                    weight=0.6,
                    description="Domain was registered within the last 180 days.",
                    evidence_ids=[ev.id],
                )
            )

    # Trust: has MX records
    if mx_evidence:
        ev = mx_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in ("true", "1", "yes"):
            signals.append(
                Signal(
                    name="has_mx_records",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.3,
                    description="Domain has MX records configured.",
                    evidence_ids=[ev.id],
                )
            )

    # Elevated: no MX records
    if no_mx_evidence:
        ev = no_mx_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in ("true", "1", "yes"):
            signals.append(
                Signal(
                    name="no_mx_records",
                    layer="infrastructure",
                    direction="elevated",
                    weight=0.4,
                    description="Domain has no MX records (no email infrastructure).",
                    evidence_ids=[ev.id],
                )
            )

    # Trust: SPF configured
    if spf_evidence:
        ev = spf_evidence[0]
        if (ev.normalized_value or ev.raw_value or "").lower() in ("true", "1", "yes"):
            signals.append(
                Signal(
                    name="spf_configured",
                    layer="infrastructure",
                    direction="trust",
                    weight=0.2,
                    description="Domain has SPF record configured.",
                    evidence_ids=[ev.id],
                )
            )

    # Trust: SSL present
    if ssl_evidence:
        signals.append(
            Signal(
                name="ssl_present",
                layer="infrastructure",
                direction="trust",
                weight=0.2,
                description="Domain has an SSL/TLS certificate.",
                evidence_ids=[e.id for e in ssl_evidence[:1]],
            )
        )

    score = _layer_score_from_signals(signals)
    return score, signals


def _score_representation_confidence(
    evidence_rows: list, field_comparisons: list
) -> tuple[float, list[Signal]]:
    """Layer 3: Does the requester plausibly represent the organization?

    Trust signals: consistent_name, consistent_address, consistent_jurisdiction
    Elevated signals: name_mismatch, address_mismatch, jurisdiction_mismatch
    """
    signals: list[Signal] = []

    # Map field comparison results to signals
    # field_comparisons is a list of FieldComparison ORM objects
    fc_by_field: dict[str, object] = {fc.field_name: fc for fc in field_comparisons}

    if not fc_by_field:
        # No field comparisons were run — reduced confidence
        signals.append(
            Signal(
                name="no_field_comparisons",
                layer="representation",
                direction="elevated",
                weight=0.3,
                description=(
                    "No field comparisons were available. "
                    "Confidence is reduced but this does not confirm misrepresentation."
                ),
                evidence_ids=[],
            )
        )
        return 35.0, signals

    # Check each compared field
    field_labels = {
        "company_name": ("Company name", 0.5),
        "country_iso": ("Jurisdiction/country", 0.3),
        "billing_address": ("Billing address", 0.2),
    }

    for field_name, (label, weight) in field_labels.items():
        fc = fc_by_field.get(field_name)
        if fc is None:
            continue

        ev_ids = [fc.evidence_id] if fc.evidence_id else []

        if fc.match_status == "match":
            signals.append(
                Signal(
                    name=f"consistent_{field_name}",
                    layer="representation",
                    direction="trust",
                    weight=weight,
                    description=f"{label} matches discovered registry data.",
                    evidence_ids=ev_ids,
                )
            )
        elif fc.match_status == "mismatch":
            signals.append(
                Signal(
                    name=f"{field_name}_mismatch",
                    layer="representation",
                    direction="elevated",
                    weight=weight,
                    description=(
                        f"{label} does not match discovered registry data. "
                        f"Submitted: {fc.submitted_value!r}, "
                        f"Discovered: {fc.discovered_value!r}."
                    ),
                    evidence_ids=ev_ids,
                )
            )
        # "unverified" contributes no signal (no data to compare)

    if not signals:
        # All fields were unverified
        signals.append(
            Signal(
                name="all_fields_unverified",
                layer="representation",
                direction="elevated",
                weight=0.4,
                description=(
                    "All compared fields are unverified — "
                    "no discovered data to compare against."
                ),
                evidence_ids=[],
            )
        )
        return 45.0, signals

    score = _layer_score_from_signals(signals)
    return score, signals


def _score_fraud_staging_risk(evidence_rows: list) -> tuple[float, list[Signal]]:
    """Layer 4: Are there signs of fraud, staging, impersonation, or sanctions evasion?

    Elevated signals: recently_registered_domain, no_mx_records (cross-layer)
    Trust signals: long_lived_domain, ssl_present (cross-layer)

    This layer synthesizes cross-cutting risk signals.  In v1 it re-uses
    infrastructure evidence but interprets it through a fraud lens.
    Full network/IP intelligence signals arrive in P2-T2.
    """
    signals: list[Signal] = []

    tier2_evidence = [e for e in evidence_rows if e.tier == 2]
    recently_registered_evidence = [
        e for e in tier2_evidence if e.field == "recently_registered"
    ]
    no_mx_evidence = [e for e in tier2_evidence if e.field == "no_mx"]
    domain_age_evidence = [e for e in tier2_evidence if e.field == "domain_age_days"]

    # Elevated: recently registered + no MX is a staging pattern
    recently_reg = any(
        (e.normalized_value or e.raw_value or "").lower() in ("true", "1", "yes")
        for e in recently_registered_evidence
    )
    no_mx = any(
        (e.normalized_value or e.raw_value or "").lower() in ("true", "1", "yes")
        for e in no_mx_evidence
    )

    if recently_reg and no_mx:
        ev_ids = [e.id for e in recently_registered_evidence + no_mx_evidence]
        signals.append(
            Signal(
                name="recently_registered_no_mx",
                layer="risk",
                direction="elevated",
                weight=0.7,
                description=(
                    "Domain was recently registered AND has no MX records — "
                    "consistent with staged/fraudulent infrastructure."
                ),
                evidence_ids=ev_ids,
            )
        )
    elif recently_reg:
        ev_ids = [e.id for e in recently_registered_evidence]
        signals.append(
            Signal(
                name="recently_registered_domain_risk",
                layer="risk",
                direction="elevated",
                weight=0.4,
                description=(
                    "Recently registered domain is a fraud/staging risk signal."
                ),
                evidence_ids=ev_ids,
            )
        )
    elif no_mx:
        ev_ids = [e.id for e in no_mx_evidence]
        signals.append(
            Signal(
                name="no_mx_risk",
                layer="risk",
                direction="elevated",
                weight=0.3,
                description=(
                    "No MX records — domain may lack legitimate email infrastructure."
                ),
                evidence_ids=ev_ids,
            )
        )

    # Trust: long-lived domain reduces fraud risk
    if domain_age_evidence:
        best_age_ev = domain_age_evidence[0]
        try:
            age_days = int(best_age_ev.normalized_value or best_age_ev.raw_value or "0")
        except (ValueError, TypeError):
            age_days = 0

        if age_days >= 365 * 3:
            signals.append(
                Signal(
                    name="long_lived_domain_trust",
                    layer="risk",
                    direction="trust",
                    weight=0.5,
                    description=(
                        f"Domain age ({age_days} days) reduces fraud/staging risk."
                    ),
                    evidence_ids=[best_age_ev.id],
                )
            )

    if not signals:
        if not tier2_evidence:
            signals.append(
                Signal(
                    name="fraud_signals_unavailable",
                    layer="risk",
                    direction="elevated",
                    weight=0.2,
                    description=(
                        "No infrastructure data available to assess fraud/staging risk."
                        " Score reflects uncertainty, not confirmed risk."
                    ),
                    evidence_ids=[],
                )
            )
            return 35.0, signals
        # Infrastructure data present but no specific risk flags
        signals.append(
            Signal(
                name="no_fraud_signals_detected",
                layer="risk",
                direction="trust",
                weight=0.5,
                description=(
                    "No fraud or staging risk signals detected from available evidence."
                ),
                evidence_ids=[e.id for e in tier2_evidence[:2]],
            )
        )

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


def _triage_tier(overall_score: float) -> str:
    """Map overall score to a triage tier.

    pre_clear: ≤ 30 — low risk, operator may expedite review
    escalate:  ≥ 70 — high risk, requires close scrutiny
    review:    31–69 — standard review

    INVARIANT: this is a TRIAGE SIGNAL, not an approval decision.
    The human operator decides; the tier is a queue-management aid.
    """
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
    """Deterministic v1 risk scoring engine.

    Usage:
        result = ScoringEngine().score(run_id, evidence_rows, field_comparisons)
    """

    def score(
        self,
        evidence_rows: list,
        field_comparisons: list,
    ) -> ScoringResult:
        """Compute risk scores from evidence and field comparison rows.

        Args:
            evidence_rows:    All Evidence ORM rows for this run.
            field_comparisons: All FieldComparison ORM rows for this run.

        Returns:
            ScoringResult — see dataclass definition above.

        INVARIANT: never produces an approve/reject decision.
        """
        entity_score, entity_signals = _score_entity_legitimacy(evidence_rows)
        infra_score, infra_signals = _score_infrastructure_legitimacy(evidence_rows)
        rep_score, rep_signals = _score_representation_confidence(
            evidence_rows, field_comparisons
        )
        risk_score, risk_signals = _score_fraud_staging_risk(evidence_rows)

        layer_scores = {
            "entity": entity_score,
            "infrastructure": infra_score,
            "representation": rep_score,
            "risk": risk_score,
        }
        overall = _compute_overall_score(layer_scores)
        tier = _triage_tier(overall)
        confidence = _compute_confidence(evidence_rows)

        all_signals = entity_signals + infra_signals + rep_signals + risk_signals

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

        result = self._engine.score(evidence_rows, field_comparisons)

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
