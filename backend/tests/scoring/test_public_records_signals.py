"""Public-records evidence in entity scoring (ticket 0093).

Plan R8–R10, AE1–AE4, KTD6, KTD11. Pure-function tests over Evidence objects
shaped like app.adapters.public_records writes them; no DB needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.evidence import Evidence
from app.scoring.engine import (
    CRITICAL_ESCALATION_SIGNALS,
    ScoringEngine,
    _compute_confidence,
)
from app.scoring.signals import entity_legitimacy_signals
from app.scoring.triage import derive_triage

FETCHED = datetime(2026, 10, 16, tzinfo=timezone.utc)
BANKRUPTCY = "public_records_open_bankruptcy"
EXCLUSION = "public_records_active_federal_exclusion"


def _ev(field, raw, normalized, *, source, tier=1, payload=None) -> Evidence:
    ev = Evidence(
        verification_run_id="run-1",
        source=source,
        tier=tier,
        field=field,
        raw_value=raw,
        normalized_value=normalized,
        confidence=0.9,
        raw_payload=payload or {},
        fetched_at=FETCHED,
    )
    ev.id = str(uuid.uuid4())
    return ev


def _bankruptcy(match="strong", status="open", filed="2025-08-12", **extra):
    payload = {
        "kind": "bankruptcy",
        "match": match,
        "status": status,
        "chapter": "11",
        "court": "United States Bankruptcy Court, D. Delaware",
        "docket_number": "25-10417",
        "filed_date": filed,
        "freshness": "fresh",
        **extra,
    }
    return _ev(
        "public_record", "bankruptcy", match, source="public_records", payload=payload
    )


def _exclusion(match="strong", status="active", classification="Firm"):
    payload = {
        "kind": "federal_exclusion",
        "match": match,
        "status": status,
        "classification": classification,
        "agency": "GSA",
        "exclusion_type": "Ineligible (Proceedings Completed)",
        "active_date": "2025-03-14",
        "sam_number": "00000000-0000-4000-8000-000000000001",
    }
    return _ev(
        "public_record",
        "federal_exclusion",
        match,
        source="public_records",
        payload=payload,
    )


def _civil(n):
    return [
        _ev(
            "public_record",
            "federal_civil",
            "unscored",
            source="public_records",
            payload={
                "kind": "federal_civil",
                "match": "strong",
                "status": "open",
                "scored": False,
            },
        )
        for _ in range(n)
    ]


def _status(source, value):
    return _ev(
        "public_records_status",
        value,
        value,
        source="public_records",
        payload={"source": source},
    )


def _bookkeeping():
    return [
        _status("courtlistener", "searched"),
        _status("sam_exclusions", "no_records"),
        _ev(
            "public_records_summary",
            "summary",
            None,
            source="public_records",
            payload={},
        ),
    ]


def _clean_company() -> list[Evidence]:
    return [
        _ev(
            "company_name",
            "Harbor Freight Lines Ltd",
            "Harbor Freight Lines Ltd",
            source="opencorporates",
        ),
        _ev("registration_status", "Active", "Active", source="opencorporates"),
        _ev("domain_age_days", "4000", "4000", source="domain", tier=2),
        _ev("mx_present", "true", "true", source="domain", tier=2),
        _ev("spf_present", "true", "true", source="domain", tier=2),
        _ev("ssl_issuer", "DigiCert", "DigiCert", source="domain", tier=2),
    ]


def _names(signals) -> set[str]:
    return {s.name for s in signals}


def _signal(result, name):
    return next((s for s in result.contributing_signals if s.name == name), None)


# --- KTD6: not registry evidence, not coverage --------------------------------


def test_public_records_rows_do_not_count_as_registry_evidence():
    names = _names(entity_legitimacy_signals(_bookkeeping() + [_bankruptcy()]))
    assert "no_registry_evidence" in names
    assert "registry_name_unconfirmed" not in names


def test_registry_signals_unchanged_when_public_records_rows_are_added():
    base = _names(entity_legitimacy_signals(_clean_company()))
    with_rows = _names(entity_legitimacy_signals(_clean_company() + _bookkeeping()))
    assert with_rows == base


def test_public_records_rows_do_not_raise_source_coverage():
    web = [_ev("web_contact_found", "true", "true", source="web", tier=3)]
    assert _compute_confidence(
        web + _bookkeeping() + [_bankruptcy()]
    ) == _compute_confidence(web)


def test_signals_still_fire_when_registry_evidence_is_missing():
    names = _names(entity_legitimacy_signals([_bankruptcy(), _exclusion()]))
    assert {"no_registry_evidence", BANKRUPTCY, EXCLUSION} <= names


# --- R8 / AE1: open bankruptcy ----------------------------------------------


def test_open_recent_bankruptcy_fires_and_cites_the_docket():
    """Covers AE1."""
    ev = _bankruptcy(filed="2025-08-12")  # 14 months before FETCHED
    (signal,) = [s for s in entity_legitimacy_signals([ev]) if s.name == BANKRUPTCY]
    assert signal.direction == "elevated" and signal.weight == 0.8
    assert signal.evidence_ids == [ev.id]
    for cited in ("25-10417", "D. Delaware", "2025-08-12"):
        assert cited in signal.description


def test_old_closed_or_weak_bankruptcies_fire_nothing():
    for ev in (
        _bankruptcy(filed="2023-06-01"),  # 40 months before FETCHED
        _bankruptcy(status="closed"),
        _bankruptcy(match="weak"),
    ):
        assert BANKRUPTCY not in _names(entity_legitimacy_signals([ev]))


def test_several_open_bankruptcies_make_one_signal_citing_all():
    a, b = _bankruptcy(docket_number="25-1"), _bankruptcy(docket_number="25-2")
    (signal,) = [s for s in entity_legitimacy_signals([a, b]) if s.name == BANKRUPTCY]
    assert set(signal.evidence_ids) == {a.id, b.id}


# --- R8 / AE2 / KTD11: active federal exclusion -------------------------------


def test_active_exclusion_fires_and_cites_agency_and_date():
    """Covers AE2."""
    ev = _exclusion()
    (signal,) = [s for s in entity_legitimacy_signals([ev]) if s.name == EXCLUSION]
    assert signal.direction == "elevated" and signal.weight == 0.9
    assert signal.evidence_ids == [ev.id]
    assert "GSA" in signal.description and "2025-03-14" in signal.description


def test_terminated_weak_or_individual_exclusions_fire_nothing():
    for ev in (
        _exclusion(status="terminated"),
        _exclusion(match="weak"),
        _exclusion(classification="Individual", match="weak"),
    ):
        assert EXCLUSION not in _names(entity_legitimacy_signals([ev]))


# --- AE3 / R11: civil cases never score ------------------------------------


def test_open_civil_cases_fire_no_signal():
    """Covers AE3."""
    names = _names(entity_legitimacy_signals(_clean_company() + _civil(5)))
    assert not {n for n in names if n.startswith("public_records")}


# --- R9 / AE4: absence is not trust ----------------------------------------


def test_no_records_scores_like_no_public_records_evidence():
    """Covers AE4."""
    base = ScoringEngine().score(_clean_company(), [])
    with_rows = ScoringEngine().score(_clean_company() + _bookkeeping(), [])
    assert with_rows.overall_score == base.overall_score
    assert with_rows.triage_tier == base.triage_tier
    assert _names(with_rows.contributing_signals) == _names(base.contributing_signals)


# --- R10 / KTD11: triage, one test per tier --------------------------------


def test_only_the_exclusion_signal_is_critical():
    assert EXCLUSION in CRITICAL_ESCALATION_SIGNALS
    assert BANKRUPTCY not in CRITICAL_ESCALATION_SIGNALS
    assert (
        "public_records_federal_litigation_as_defendant"
        not in CRITICAL_ESCALATION_SIGNALS
    )


def test_clean_company_stays_pre_clear():
    assert ScoringEngine().score(_clean_company(), []).triage_tier == "pre_clear"


def test_active_exclusion_forces_escalate_on_a_clean_company():
    ev = _exclusion()
    result = ScoringEngine().score(_clean_company() + [ev], [])
    assert result.overall_score < 70  # the score alone would not escalate...
    assert result.triage_tier == "escalate"  # ...the exclusion must
    triage = derive_triage(result)
    assert (triage.tier, triage.critical_signals) == ("escalate", [EXCLUSION])


def test_bankruptcy_alone_keeps_the_score_based_tier():
    base = ScoringEngine().score(_clean_company(), [])
    result = ScoringEngine().score(_clean_company() + [_bankruptcy()], [])
    signal = _signal(result, BANKRUPTCY)
    assert signal is not None
    assert result.overall_score > base.overall_score
    expected = "pre_clear" if result.overall_score <= 30 else "review"
    assert result.triage_tier == expected  # never forced to escalate
