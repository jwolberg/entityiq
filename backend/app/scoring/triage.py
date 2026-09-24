"""Triage tier derivation (P2-T7).

Derives a structured triage result from the four-layer ``ScoringResult``
produced by ``ScoringEngine``.

INVARIANT: triage orders work; it NEVER auto-approves.
A ``pre_clear`` tier means the system assessed the entity as low-risk and
the operator may expedite their review — but a human action is still required
to approve.  No approval state is set or returned by this module.

Triage tiers
------------
``pre_clear``   overall_score ≤ TRIAGE_PRE_CLEAR_MAX (30)
                Low observed risk; operator may fast-track but MUST approve.
``review``      31 ≤ overall_score ≤ 69
                Standard review — no specific escalation signal detected.
``escalate``    overall_score ≥ TRIAGE_ESCALATE_MIN (70)
                Elevated risk or critical signal (sanctions, mismatch stack)
                — requires close operator scrutiny.

These thresholds are imported from ``engine`` to keep them in one place.

Usage
-----
    from app.scoring.triage import derive_triage
    from app.scoring.engine import ScoringEngine

    result = ScoringEngine().score(evidence_rows, field_comparisons)
    triage = derive_triage(result)
    assert triage.tier in ("pre_clear", "review", "escalate")
    # triage.tier is NEVER "approved" or "rejected"
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.scoring.engine import (
    CRITICAL_ESCALATION_SIGNALS,
    TRIAGE_ESCALATE_MIN,
    TRIAGE_PRE_CLEAR_MAX,
    ScoringResult,
)


@dataclass
class TriageResult:
    """Output of derive_triage().

    Attributes
    ----------
    tier:
        One of ``pre_clear`` | ``review`` | ``escalate``.
        NEVER ``approved`` or ``rejected`` — triage is advisory.
    reason:
        Human-readable summary of why this tier was assigned.
    critical_signals:
        Names of any signals that drove an escalation (empty for review/pre_clear).
    advisory_note:
        Operator-facing note emphasising that triage is a queue-management signal,
        not an approval decision.
    """

    tier: str
    reason: str
    critical_signals: list[str] = field(default_factory=list)
    advisory_note: str = (
        "Triage is a queue-management signal only. "
        "A human operator must approve or reject this registration."
    )

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "reason": self.reason,
            "critical_signals": self.critical_signals,
            "advisory_note": self.advisory_note,
        }


# Names of signals that alone justify escalation regardless of overall score
# (defined in engine so the persisted tier uses the same rule).
_CRITICAL_ESCALATION_SIGNALS = CRITICAL_ESCALATION_SIGNALS


def derive_triage(result: ScoringResult) -> TriageResult:
    """Derive a TriageResult from a ScoringResult.

    Logic
    -----
    1. Check for any critical signals (sanctions hit, high ASN reuse) — these
       force an ``escalate`` tier regardless of overall score.
    2. Otherwise apply the standard threshold from ``engine``:
       overall_score ≤ 30 → pre_clear; ≥ 70 → escalate; else review.

    INVARIANT: returns a tier in ("pre_clear", "review", "escalate").
    Never sets an approval state.
    """
    # --- Step 1: critical-signal override ---
    critical: list[str] = [
        sig.name
        for sig in result.contributing_signals
        if sig.name in _CRITICAL_ESCALATION_SIGNALS
    ]

    if critical:
        return TriageResult(
            tier="escalate",
            reason=(
                f"Critical risk signal(s) detected: {', '.join(critical)}. "
                "Requires close operator scrutiny."
            ),
            critical_signals=critical,
        )

    # --- Step 2: score-based threshold ---
    score = result.overall_score

    if score <= TRIAGE_PRE_CLEAR_MAX:
        elevated = [
            s.name for s in result.contributing_signals if s.direction == "elevated"
        ]
        if elevated:
            reason = (
                f"Overall score {score:.1f} is below the pre-clear threshold "
                f"({TRIAGE_PRE_CLEAR_MAX}). "
                f"Minor elevated signal(s) present: {', '.join(elevated[:3])}."
            )
        else:
            reason = (
                f"Overall score {score:.1f} is below the pre-clear threshold "
                f"({TRIAGE_PRE_CLEAR_MAX}). "
                "No elevated risk signals detected."
            )
        return TriageResult(
            tier="pre_clear",
            reason=reason,
            critical_signals=[],
        )

    if score >= TRIAGE_ESCALATE_MIN:
        elevated = [
            s.name for s in result.contributing_signals if s.direction == "elevated"
        ]
        return TriageResult(
            tier="escalate",
            reason=(
                f"Overall score {score:.1f} meets or exceeds escalation threshold "
                f"({TRIAGE_ESCALATE_MIN}). "
                f"Elevated signal(s): {', '.join(elevated[:5]) or 'none named'}."
            ),
            critical_signals=[],
        )

    # Standard review
    elevated = [
        s.name for s in result.contributing_signals if s.direction == "elevated"
    ]
    trust = [s.name for s in result.contributing_signals if s.direction == "trust"]
    return TriageResult(
        tier="review",
        reason=(
            f"Overall score {score:.1f} is in the standard review band "
            f"({TRIAGE_PRE_CLEAR_MAX + 1}–{TRIAGE_ESCALATE_MIN - 1}). "
            f"Elevated signal(s): {', '.join(elevated[:3]) or 'none'}; "
            f"trust signal(s): {', '.join(trust[:3]) or 'none'}."
        ),
        critical_signals=[],
    )
