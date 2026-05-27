"""Explainability payload assembly (P2-T7).

Produces a structured ``ExplainabilityPayload`` from a ``ScoringResult``,
evidence rows, and field comparisons.  This payload is surfaced in the
assembled report so the operator can understand:

  WHY the score exists
  WHAT caused concern
  WHICH sources were used

PRD § Explainability Requirements:
  Every risk score MUST show evidence, source attribution, contributing
  signals, and highlight mismatches.

Structure
---------
ExplainabilityPayload
  triage_tier       — string ("pre_clear" | "review" | "escalate")
  overall_score     — float 0–100
  confidence        — float 0–1
  layers            — one LayerExplanation per scoring layer
    LayerExplanation
      layer         — "entity" | "infrastructure" | "representation" | "risk"
      score         — float 0–100
      signals       — list[SignalDetail]
        SignalDetail
          name, direction, weight, description, evidence_ids
      sources       — list of distinct sources contributing to this layer
  mismatches        — list[MismatchDetail] from field comparisons
    MismatchDetail
      field_name, submitted_value, discovered_value, match_status, evidence_id
  source_coverage   — dict mapping source name → {tier, evidence_count}

Usage
-----
    from app.scoring.explain import build_explainability

    payload = build_explainability(
        result=scoring_result,
        evidence_rows=evidence_rows,
        field_comparisons=field_comparisons,
    )
    report_summary["explainability"] = payload.to_dict()
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.scoring.engine import ScoringResult


@dataclass
class SignalDetail:
    """One signal's contribution within a layer explanation."""

    name: str
    layer: str
    direction: str  # "trust" | "elevated"
    weight: float
    description: str
    evidence_ids: list[str]

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
class MismatchDetail:
    """A field comparison entry (match / mismatch / unverified)."""

    field_name: str
    submitted_value: str | None
    discovered_value: str | None
    match_status: str
    evidence_id: str | None

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "submitted_value": self.submitted_value,
            "discovered_value": self.discovered_value,
            "match_status": self.match_status,
            "evidence_id": self.evidence_id,
        }


@dataclass
class LayerExplanation:
    """Explanation for one of the four scoring layers."""

    layer: str
    score: float
    signals: list[SignalDetail] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "layer": self.layer,
            "score": self.score,
            "signals": [s.to_dict() for s in self.signals],
            "sources": self.sources,
        }


@dataclass
class ExplainabilityPayload:
    """Full explainability payload for a scored verification run.

    This is what operators see in the report to understand the scoring
    decision.  It is NOT a decision — it is evidence attribution.
    """

    triage_tier: str
    overall_score: float
    confidence: float
    layers: list[LayerExplanation] = field(default_factory=list)
    mismatches: list[MismatchDetail] = field(default_factory=list)
    source_coverage: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "triage_tier": self.triage_tier,
            "overall_score": self.overall_score,
            "confidence": self.confidence,
            "layers": [layer.to_dict() for layer in self.layers],
            "mismatches": [m.to_dict() for m in self.mismatches],
            "source_coverage": self.source_coverage,
        }


# Layer ordering for consistent presentation
_LAYER_ORDER = ("entity", "infrastructure", "representation", "risk")

# Map layer name → (score attribute name on ScoringResult)
_LAYER_SCORE_ATTRS = {
    "entity": "entity_score",
    "infrastructure": "infrastructure_score",
    "representation": "representation_score",
    "risk": "risk_score",
}


def build_explainability(
    result: ScoringResult,
    evidence_rows: list,
    field_comparisons: list,
) -> ExplainabilityPayload:
    """Build a structured explainability payload from a ScoringResult.

    Args:
        result:            Output of ScoringEngine.score().
        evidence_rows:     Evidence ORM rows (or simple objects with .id, .source,
                           .tier, .field attributes) for the run.
        field_comparisons: FieldComparison ORM rows (or simple objects with
                           .field_name, .submitted_value, .discovered_value,
                           .match_status, .evidence_id).

    Returns:
        ExplainabilityPayload — structured, JSON-serialisable via .to_dict().
    """
    # --- Build source coverage map ---
    source_coverage: dict[str, dict] = {}
    for ev in evidence_rows:
        src = ev.source
        if src not in source_coverage:
            source_coverage[src] = {
                "source": src,
                "tier": ev.tier,
                "evidence_count": 0,
            }
        source_coverage[src]["evidence_count"] += 1

    # --- Build evidence ID → source map for signal attribution ---
    ev_source_map: dict[str, str] = {ev.id: ev.source for ev in evidence_rows}

    # --- Group signals by layer ---
    signals_by_layer: dict[str, list] = {layer: [] for layer in _LAYER_ORDER}
    for sig in result.contributing_signals:
        if sig.layer in signals_by_layer:
            signals_by_layer[sig.layer].append(sig)

    # --- Build LayerExplanation for each layer ---
    layers: list[LayerExplanation] = []
    for layer_name in _LAYER_ORDER:
        layer_signals = signals_by_layer[layer_name]
        score = getattr(result, _LAYER_SCORE_ATTRS[layer_name], 0.0)

        # Collect distinct sources that contributed evidence to this layer's signals
        layer_sources: set[str] = set()
        signal_details: list[SignalDetail] = []

        for sig in layer_signals:
            for ev_id in sig.evidence_ids:
                src = ev_source_map.get(ev_id)
                if src:
                    layer_sources.add(src)

            signal_details.append(
                SignalDetail(
                    name=sig.name,
                    layer=sig.layer,
                    direction=sig.direction,
                    weight=sig.weight,
                    description=sig.description,
                    evidence_ids=sig.evidence_ids,
                )
            )

        layers.append(
            LayerExplanation(
                layer=layer_name,
                score=score,
                signals=signal_details,
                sources=sorted(layer_sources),
            )
        )

    # --- Build mismatch details from field comparisons ---
    mismatches: list[MismatchDetail] = []
    for fc in field_comparisons:
        mismatches.append(
            MismatchDetail(
                field_name=fc.field_name,
                submitted_value=fc.submitted_value,
                discovered_value=fc.discovered_value,
                match_status=fc.match_status,
                evidence_id=fc.evidence_id,
            )
        )

    return ExplainabilityPayload(
        triage_tier=result.triage_tier,
        overall_score=result.overall_score,
        confidence=result.confidence,
        layers=layers,
        mismatches=mismatches,
        source_coverage=source_coverage,
    )
