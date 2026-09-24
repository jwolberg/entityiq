"""Tax-ID evidence in entity-legitimacy scoring (tickets 0007, 0009).

Pure-function tests over Evidence objects; no DB needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.evidence import Evidence
from app.scoring.signals import entity_legitimacy_signals


def _ev(field: str, value: str, *, source: str = "tax_id", tier: int = 1) -> Evidence:
    ev = Evidence(
        verification_run_id="run-1",
        source=source,
        tier=tier,
        field=field,
        raw_value=value,
        normalized_value=value,
        confidence=0.9,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    ev.id = str(uuid.uuid4())
    return ev


def _names(signals) -> set[str]:
    return {s.name for s in signals}


def test_tax_id_rows_do_not_count_as_registry_evidence():
    """Registry signals must behave exactly as before the tax-ID source existed."""
    only_tax = [_ev("tax_id_status", "verified"), _ev("tax_id_name_match", "match")]

    names = _names(entity_legitimacy_signals(only_tax))

    assert "no_registry_evidence" in names
    assert "registry_name_unconfirmed" not in names


def test_registry_signals_unchanged_when_tax_id_rows_are_added():
    registry = [
        _ev("company_name", "Acme Corporation", source="opencorporates"),
        _ev("registration_status", "Active", source="opencorporates"),
    ]
    tax = [_ev("tax_id_status", "verified"), _ev("tax_id_name_match", "match")]

    without = _names(entity_legitimacy_signals(registry))
    with_tax = _names(entity_legitimacy_signals(registry + tax))

    registry_names = {n for n in with_tax if not n.startswith("tax_id")}
    assert registry_names == without


# ---------------------------------------------------------------------------
# Ticket 0009 (IC1-T3): tax-ID signals on the entity layer
# ---------------------------------------------------------------------------

from app.scoring.engine import _score_entity_legitimacy  # noqa: E402

_REGISTRY = [_ev("company_name", "Acme Corporation", source="opencorporates")]


def _signal(signals, name):
    return next(s for s in signals if s.name == name)


def test_verified_active_with_name_match_is_a_trust_signal_citing_evidence():
    status = _ev("tax_id_status", "verified")
    match = _ev("tax_id_name_match", "match")

    sig = _signal(
        entity_legitimacy_signals(_REGISTRY + [status, match]), "tax_id_verified_active"
    )

    assert sig.direction == "trust"
    assert sig.layer == "entity"
    assert set(sig.evidence_ids) == {status.id, match.id}


def test_verified_without_name_match_is_not_verified_active():
    signals = entity_legitimacy_signals(
        _REGISTRY
        + [_ev("tax_id_status", "verified"), _ev("tax_id_name_match", "mismatch")]
    )
    names = _names(signals)
    assert "tax_id_verified_active" not in names
    assert "tax_id_name_mismatch" in names


def test_not_found_is_elevated():
    status = _ev("tax_id_status", "not_found")
    sig = _signal(entity_legitimacy_signals(_REGISTRY + [status]), "tax_id_not_found")
    assert sig.direction == "elevated"
    assert sig.evidence_ids == [status.id]


def test_name_mismatch_is_elevated():
    mismatch = _ev("tax_id_name_match", "mismatch")
    sig = _signal(
        entity_legitimacy_signals(
            _REGISTRY + [_ev("tax_id_status", "verified"), mismatch]
        ),
        "tax_id_name_mismatch",
    )
    assert sig.direction == "elevated"
    assert sig.evidence_ids == [mismatch.id]


def test_inactive_or_dissolved_is_elevated():
    status = _ev("tax_id_status", "inactive")
    sig = _signal(
        entity_legitimacy_signals(
            _REGISTRY + [status, _ev("tax_id_name_match", "match")]
        ),
        "tax_id_inactive_or_dissolved",
    )
    assert sig.direction == "elevated"
    assert "tax_id_verified_active" not in _names(
        entity_legitimacy_signals(
            _REGISTRY + [status, _ev("tax_id_name_match", "match")]
        )
    )


def test_unavailable_adds_no_signal_and_no_penalty():
    """No tax-ID evidence at all (no input / provider down / non-US)."""
    baseline_score, baseline = _score_entity_legitimacy(_REGISTRY)

    assert not any(s.name.startswith("tax_id") for s in baseline)
    assert "valid_tax_id" not in _names(baseline)
    # Same evidence, same score: the absence of the source costs nothing.
    assert _score_entity_legitimacy(list(_REGISTRY))[0] == baseline_score


def test_scores_move_in_the_right_direction():
    base, _ = _score_entity_legitimacy(_REGISTRY)
    verified, _ = _score_entity_legitimacy(
        _REGISTRY
        + [_ev("tax_id_status", "verified"), _ev("tax_id_name_match", "match")]
    )
    not_found, _ = _score_entity_legitimacy(
        _REGISTRY + [_ev("tax_id_status", "not_found")]
    )

    assert verified < base < not_found


def test_every_tax_id_signal_cites_evidence():
    for rows in (
        [_ev("tax_id_status", "verified"), _ev("tax_id_name_match", "match")],
        [_ev("tax_id_status", "not_found")],
        [_ev("tax_id_status", "inactive"), _ev("tax_id_name_match", "mismatch")],
    ):
        for sig in entity_legitimacy_signals(_REGISTRY + rows):
            if sig.name.startswith("tax_id"):
                assert sig.evidence_ids, sig.name


def test_tax_id_signals_still_fire_when_registry_evidence_is_missing():
    """OpenCorporates is often unavailable (no token); FEIN evidence still counts."""
    names = _names(
        entity_legitimacy_signals(
            [_ev("tax_id_status", "verified"), _ev("tax_id_name_match", "match")]
        )
    )
    assert "no_registry_evidence" in names
    assert "tax_id_verified_active" in names


def test_verified_fein_without_a_registered_name_still_earns_weaker_trust():
    """Review follow-up: a provider may confirm the FEIN but return no name."""
    status = _ev("tax_id_status", "verified")
    signals = entity_legitimacy_signals(_REGISTRY + [status])

    sig = _signal(signals, "tax_id_verified_name_unconfirmed")
    assert sig.direction == "trust"
    assert sig.evidence_ids == [status.id]
    assert (
        sig.weight
        < _signal(
            entity_legitimacy_signals(
                _REGISTRY + [status, _ev("tax_id_name_match", "match")]
            ),
            "tax_id_verified_active",
        ).weight
    )
    assert "tax_id_verified_active" not in _names(signals)
