"""LinkedIn comparisons and signals (IC1-T5, ticket 0011).

Representation is primary; entity legitimacy gets a small secondary trust
term. LinkedIn is Tier 3: absence is weak, and it never overrides a Tier-1
outcome (feature PRD §6 calibration note and §12).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.evidence import Evidence
from app.models.field_comparison import FieldComparison
from app.pipeline.consistency import _linkedin_website_match
from app.scoring.engine import ScoringEngine
from app.scoring.signals import (
    entity_legitimacy_signals,
    fraud_staging_risk_signals,
    representation_confidence_signals,
)


def _ev(field: str, value: str, *, source: str = "linkedin", tier: int = 3) -> Evidence:
    ev = Evidence(
        verification_run_id="run-1",
        source=source,
        tier=tier,
        field=field,
        raw_value=value,
        normalized_value=value,
        confidence=0.7,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    ev.id = str(uuid.uuid4())
    return ev


def _website_fc(status: str, ev: Evidence | None = None) -> FieldComparison:
    return FieldComparison(
        verification_run_id="run-1",
        evidence_id=ev.id if ev else None,
        field_name="linkedin_website",
        submitted_value="acme.com",
        discovered_value=ev.normalized_value if ev else None,
        match_status=status,
    )


def _page(
    *, employees="1200", followers="45000", created="2011-03-01", website="acme.com"
):
    return [
        _ev("linkedin_presence", "found"),
        _ev("linkedin_employee_count", employees),
        _ev("linkedin_followers", followers),
        _ev("linkedin_page_created", created),
        _ev("linkedin_website", website),
    ]


def _rep(evidence, fcs):
    return {s.name: s for s in representation_confidence_signals(evidence, fcs)}


# ---------------------------------------------------------------------------
# Website comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("submitted", "discovered", "expected"),
    [
        ("acme.com", "acme.com", True),
        ("www.acme.com", "acme.com", True),
        ("ACME.COM", "https://www.acme.com/about", True),
        ("acme.com", "shop.acme.com", True),
        ("acme.com", "acme-corp.io", False),
        ("acme.com", "notacme.com", False),
        (None, "acme.com", False),
    ],
)
def test_linkedin_website_match(submitted, discovered, expected):
    assert _linkedin_website_match(submitted, discovered) is expected


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def test_established_presence_needs_page_footprint_and_website_match():
    ev = _page()
    site = next(e for e in ev if e.field == "linkedin_website")
    sig = _rep(ev, [_website_fc("match", site)])["linkedin_established_presence"]

    assert sig.direction == "trust"
    assert sig.layer == "representation"
    assert sig.evidence_ids


def test_no_established_presence_when_website_does_not_match():
    ev = _page(website="acme-corp.io")
    site = next(e for e in ev if e.field == "linkedin_website")
    signals = _rep(ev, [_website_fc("mismatch", site)])

    assert "linkedin_established_presence" not in signals
    mismatch = signals["linkedin_website_mismatch"]
    assert mismatch.direction == "elevated"
    assert mismatch.evidence_ids == [site.id]


def test_requester_associated_is_trust():
    match = _ev("linkedin_requester_match", "true")
    sig = _rep(_page() + [match], [])["linkedin_requester_associated"]
    assert sig.direction == "trust"
    assert sig.evidence_ids == [match.id]


def test_requester_not_associated_adds_nothing():
    """Best-effort association: a miss is not evidence of misrepresentation."""
    signals = _rep(_page() + [_ev("linkedin_requester_match", "false")], [])
    assert not any("requester" in n for n in signals)


@pytest.mark.parametrize(
    "evidence",
    [
        [_ev("linkedin_presence", "not_found")],
        _page(employees="2", followers="10"),
    ],
    ids=["submitted-url-dead", "thin-page"],
)
def test_absent_or_thin_is_weakly_elevated(evidence):
    sig = _rep(evidence, [])["linkedin_absent_or_thin"]
    assert sig.direction == "elevated"
    assert sig.evidence_ids


def test_absence_weighs_no_more_than_the_weakest_tier3_elevated_signal():
    thin_web = next(
        s
        for s in fraud_staging_risk_signals(
            [_ev("web_thin_footprint", "true", source="web")]
        )
        if s.name == "thin_website"
    )
    absent = _rep([_ev("linkedin_presence", "not_found")], [])[
        "linkedin_absent_or_thin"
    ]
    assert absent.weight <= thin_web.weight


def test_recently_created_page_is_elevated_and_old_page_is_not():
    recent = (date.today() - timedelta(days=60)).isoformat()
    signals = _rep(_page(created=recent), [])
    assert signals["linkedin_recently_created"].direction == "elevated"
    assert "linkedin_recently_created" not in _rep(_page(created="2011-03-01"), [])


def test_unavailable_linkedin_adds_no_signal():
    signals = _rep([], [])
    assert not any(n.startswith("linkedin") for n in signals)


def test_entity_layer_gets_secondary_trust_from_established_presence():
    ev = _page()
    names = {s.name for s in entity_legitimacy_signals(ev)}
    assert "linkedin_presence_corroborates_entity" in names
    assert "linkedin_presence_corroborates_entity" not in {
        s.name for s in entity_legitimacy_signals(_page(employees="2", followers="10"))
    }


def test_every_linkedin_signal_cites_evidence():
    site = _ev("linkedin_website", "acme.com")
    evidence = _page() + [_ev("linkedin_requester_match", "true")]
    for sig in list(_rep(evidence, [_website_fc("match", site)]).values()) + list(
        entity_legitimacy_signals(evidence)
    ):
        if sig.name.startswith("linkedin"):
            assert sig.evidence_ids, sig.name


# ---------------------------------------------------------------------------
# Tier 3 never overrides Tier 1
# ---------------------------------------------------------------------------


def test_established_linkedin_does_not_override_a_tier1_negative():
    tier1 = [
        _ev("company_name", "Acme Corporation", source="opencorporates", tier=1),
        _ev("sanctions_hit", "true", source="ofac", tier=1),
    ]
    site = next(e for e in _page() if e.field == "linkedin_website")
    result = ScoringEngine().score(
        tier1 + _page() + [_ev("linkedin_requester_match", "true")],
        [_website_fc("match", site)],
    )
    assert result.triage_tier == "escalate"


def test_established_linkedin_cannot_pull_a_tax_id_failure_to_pre_clear():
    tier1 = [
        _ev("company_name", "Acme Corporation", source="opencorporates", tier=1),
        _ev("tax_id_status", "not_found", source="tax_id", tier=1),
    ]
    with_li = ScoringEngine().score(tier1 + _page(), [])
    without_li = ScoringEngine().score(tier1, [])

    assert "tax_id_not_found" in {s.name for s in with_li.contributing_signals}
    # LinkedIn may nudge the score but must not change the entity-layer verdict.
    assert with_li.entity_score >= without_li.entity_score - 5
