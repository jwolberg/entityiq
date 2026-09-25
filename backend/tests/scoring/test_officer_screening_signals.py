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
