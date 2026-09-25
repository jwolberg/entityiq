"""Officer/owner screening evidence in scoring (tickets 0081, 0082).

Pure-function tests over Evidence objects; no DB needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.evidence import Evidence
from app.scoring.engine import _compute_confidence
from app.scoring.signals import entity_legitimacy_signals


def _ev(field: str, value: str, *, source: str, tier: int = 1) -> Evidence:
    ev = Evidence(
        verification_run_id="run-1",
        source=source,
        tier=tier,
        field=field,
        raw_value=value,
        normalized_value=value,
        confidence=0.9,
        raw_payload={},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    ev.id = str(uuid.uuid4())
    return ev


def _officer(disposition: str) -> Evidence:
    return _ev("officer_screening_result", disposition, source="officer_screening")


def _names(signals) -> set[str]:
    return {s.name for s in signals}


# --- Ticket 0081: a person's screening is not evidence about the company -----


def test_officer_rows_do_not_count_as_registry_evidence():
    names = _names(entity_legitimacy_signals([_officer("CLEAR")]))

    assert "no_registry_evidence" in names
    assert "registry_name_unconfirmed" not in names


def test_officer_rows_do_not_raise_source_coverage():
    web = [_ev("web_contact_found", "true", source="web", tier=3)]

    assert _compute_confidence(web + [_officer("CLEAR")]) == _compute_confidence(web)


# --- Ticket 0082: officer outcomes drive the company's tier -----------------

from app.scoring.engine import CRITICAL_ESCALATION_SIGNALS, ScoringEngine  # noqa: E402


def _clean_company() -> list[Evidence]:
    return [
        _ev("company_name", "Harbor Freight Lines Ltd", source="opencorporates"),
        _ev("registration_status", "Active", source="opencorporates"),
        _ev("domain_age_days", "4000", source="domain", tier=2),
        _ev("mx_present", "true", source="domain", tier=2),
        _ev("spf_present", "true", source="domain", tier=2),
        _ev("ssl_issuer", "DigiCert", source="domain", tier=2),
    ]


def _person(disposition: str, relationships=("owner",), roles=("director",)):
    ev = _officer(disposition)
    ev.raw_payload = {"relationships": list(relationships), "roles": list(roles)}
    return ev


def _signal(result, name):
    return next((s for s in result.contributing_signals if s.name == name), None)


def test_baseline_clean_company_is_pre_clear():
    result = ScoringEngine().score(_clean_company(), [])
    assert result.triage_tier == "pre_clear"


def test_clear_officer_changes_nothing():
    base = ScoringEngine().score(_clean_company(), [])
    result = ScoringEngine().score(_clean_company() + [_person("CLEAR")], [])

    assert result.triage_tier == "pre_clear"
    assert result.overall_score == base.overall_score
    assert not {s.name for s in result.contributing_signals} - {
        s.name for s in base.contributing_signals
    }


def test_review_officer_raises_score_without_forcing_escalate():
    base = ScoringEngine().score(_clean_company(), [])
    review = _person("REVIEW")
    result = ScoringEngine().score(_clean_company() + [review], [])

    signal = _signal(result, "officer_screening_review")
    assert signal is not None
    assert signal.direction == "elevated"
    assert signal.evidence_ids == [review.id]
    assert "officer_screening_review" not in CRITICAL_ESCALATION_SIGNALS
    assert result.overall_score > base.overall_score
    assert result.triage_tier != "escalate"


def test_match_forces_escalate_on_an_otherwise_clean_company():
    match = _person("MATCH", relationships=("officer", "owner"))
    result = ScoringEngine().score(_clean_company() + [match], [])

    signal = _signal(result, "officer_sanctions_match")
    assert signal is not None
    assert signal.evidence_ids == [match.id]
    assert "officer_sanctions_match" in CRITICAL_ESCALATION_SIGNALS
    assert result.overall_score < 70  # the score alone would not escalate...
    assert result.triage_tier == "escalate"  # ...the officer match must
    # The report's triage section agrees and names the critical signal.
    from app.scoring.triage import derive_triage

    triage = derive_triage(result)
    assert (triage.tier, triage.critical_signals) == (
        "escalate",
        ["officer_sanctions_match"],
    )
    # Explains who, by relationship and role, never by name.
    assert "officer" in signal.description and "director" in signal.description


def test_one_signal_cites_every_matching_person():
    a, b = _person("MATCH"), _person("MATCH", relationships=("officer",))
    result = ScoringEngine().score(_clean_company() + [a, b], [])

    signal = _signal(result, "officer_sanctions_match")
    assert sorted(signal.evidence_ids) == sorted([a.id, b.id])
    assert "2 " in signal.description


def test_unavailable_screening_adds_no_signal():
    base = ScoringEngine().score(_clean_company(), [])
    result = ScoringEngine().score(_clean_company() + [_person("unavailable")], [])

    assert result.overall_score == base.overall_score
    assert result.triage_tier == "pre_clear"
