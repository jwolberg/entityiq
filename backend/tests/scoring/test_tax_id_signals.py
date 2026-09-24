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
