"""Unavailable identity sources show up in the report's sources list (0012).

The operator UI tells "not available" apart from "no data" by the source's
status, so the new stages must map to their evidence source names.
"""

import pytest

from app.scoring.report import _build_summary


@pytest.mark.parametrize(
    ("stage", "source", "tier"),
    [
        ("verify_tax_id", "tax_id", 1),
        ("verify_linkedin", "linkedin", 3),
        # Officers/owners from registries (ticket 0080): a coverage gap.
        ("collect_people", "registry_people", 1),
        # Screening not configured (ticket 0081): officers went unscreened.
        ("screen_people", "officer_screening", 1),
    ],
)
def test_unavailable_identity_stage_is_listed_as_unavailable_source(
    stage, source, tier
):
    summary = _build_summary([], [], None, {stage: "unavailable"})
    entry = next(s for s in summary["sources"] if s["source"] == source)
    assert entry["status"] == "unavailable"
    assert entry["tier"] == tier
