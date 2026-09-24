"""Tests for P2-T1 — Entity candidate resolution stage.

All tests are fully offline and deterministic — the stage performs no network
I/O or DB writes.  A minimal SQLAlchemy session is provided to satisfy the
stage contract but is not used.

Test scenarios:
  - Clear name + domain → single high-confidence candidate.
  - Ambiguous input (two similar candidates injected) → multiple candidates
    flagged for the risk model (conflict_signal=True).
  - Missing name and domain → no_match status.
  - Name-only submission → scores from name tokens alone.
  - Domain-only submission → scores from domain match alone.
  - resolve_candidates() — unit tests for the core logic.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest

from app.pipeline.resolve import (
    _CONFLICT_GAP,
    _STRONG_MATCH_THRESHOLD,
    ResolveEntityCandidatesStage,
    _domain_score,
    _name_score,
    _overall_score,
    resolve_candidates,
)

# ---------------------------------------------------------------------------
# Unit tests: scoring helpers
# ---------------------------------------------------------------------------


def test_name_score_identical():
    assert _name_score("Acme Corporation", "Acme Corporation") == 1.0


def test_name_score_different():
    assert _name_score("Acme Corporation", "Widget Factory") == 0.0


def test_name_score_partial_overlap():
    score = _name_score("Acme Software", "Acme Systems")
    assert score > 0.0
    assert score <= 1.0


def test_name_score_none_returns_zero():
    assert _name_score(None, "Acme Corp") == 0.0
    assert _name_score("Acme Corp", None) == 0.0


def test_domain_score_exact_match():
    assert _domain_score("acme.com", "acme.com") == 1.0


def test_domain_score_www_stripped():
    assert _domain_score("www.acme.com", "acme.com") == 1.0


def test_domain_score_registered_domain_match():
    score = _domain_score("app.acme.com", "acme.com")
    assert score == 0.8


def test_domain_score_no_match():
    assert _domain_score("acme.com", "widget.io") == 0.0


def test_domain_score_none_returns_zero():
    assert _domain_score(None, "acme.com") == 0.0


def test_overall_score_weighting():
    # 0.6 * name + 0.4 * domain
    assert _overall_score(1.0, 1.0) == 1.0
    assert _overall_score(1.0, 0.0) == pytest.approx(0.6)
    assert _overall_score(0.0, 1.0) == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Unit tests: resolve_candidates() core logic
# ---------------------------------------------------------------------------


def test_single_clear_name_and_domain():
    """Clear name + domain → single_match with high confidence."""
    normalized = {
        "company_name": "Acme Corporation",
        "domain": "acme.com",
    }
    result = resolve_candidates(normalized)

    assert result["status"] == "single_match"
    assert len(result["candidates"]) == 1
    top = result["top_candidate"]
    assert top is not None
    assert top["overall_score"] >= _STRONG_MATCH_THRESHOLD
    assert result["conflict_signal"] is False


def test_no_name_no_domain_returns_no_match():
    """Empty normalized context → no_match."""
    result = resolve_candidates({})
    assert result["status"] == "no_match"
    assert result["candidates"] == []
    assert result["top_candidate"] is None
    assert result["conflict_signal"] is False


def test_name_only_returns_single_match():
    """Name without domain → single candidate from name alone."""
    normalized = {"company_name": "Acme Corporation", "domain": None}
    result = resolve_candidates(normalized)
    assert result["status"] == "single_match"
    assert result["top_candidate"] is not None


def test_domain_only_returns_single_match():
    """Domain without name → single candidate from domain alone."""
    normalized = {"company_name": None, "domain": "acme.com"}
    result = resolve_candidates(normalized)
    assert result["status"] == "single_match"


def test_ambiguous_input_sets_conflict_signal():
    """Two candidates within conflict gap → conflict_signal=True.

    We patch _score_and_rank to return a pre-built ranked list with two
    candidates within _CONFLICT_GAP of each other and below _STRONG_MATCH_THRESHOLD.
    This tests the conflict-detection logic in resolve_candidates() directly.
    """
    close_score = _STRONG_MATCH_THRESHOLD - 0.1  # below strong threshold
    ranked = [
        {
            "name": "Acme Corp",
            "domain": "acme.com",
            "name_score": close_score,
            "domain_score": close_score,
            "overall_score": close_score,
        },
        {
            "name": "Acme Corporation",
            "domain": "acme.com",
            "name_score": close_score - (_CONFLICT_GAP / 2),
            "domain_score": close_score - (_CONFLICT_GAP / 2),
            "overall_score": close_score - (_CONFLICT_GAP / 2),
        },
    ]

    with mock.patch("app.pipeline.resolve._score_and_rank", return_value=ranked):
        result = resolve_candidates({"company_name": "Acme Corp", "domain": "acme.com"})

    assert result["conflict_signal"] is True
    assert result["status"] == "ambiguous"
    assert len(result["candidates"]) == 2


def test_no_conflict_when_top_is_strong_match():
    """Even with two candidates, no conflict if top is a strong match."""
    strong_score = _STRONG_MATCH_THRESHOLD + 0.01
    second_score = strong_score - (_CONFLICT_GAP / 2)

    ranked = [
        {
            "name": "Acme Corp",
            "domain": "acme.com",
            "name_score": strong_score,
            "domain_score": strong_score,
            "overall_score": strong_score,
        },
        {
            "name": "Acme Corp Ltd",
            "domain": "acme.com",
            "name_score": second_score,
            "domain_score": second_score,
            "overall_score": second_score,
        },
    ]

    with mock.patch("app.pipeline.resolve._score_and_rank", return_value=ranked):
        result = resolve_candidates({"company_name": "Acme Corp", "domain": "acme.com"})

    assert result["conflict_signal"] is False


def test_no_conflict_when_gap_is_large():
    """Two candidates but large score gap → no conflict signal."""
    # With a gap > _CONFLICT_GAP between top and second, no conflict is emitted.
    ranked = [
        {
            "name": "Acme Corp",
            "domain": "acme.com",
            "name_score": 0.95,
            "domain_score": 0.95,
            "overall_score": 0.95,
        },
        {
            "name": "Totally Different",
            "domain": "other.org",
            "name_score": 0.4,
            "domain_score": 0.4,
            "overall_score": 0.4,
        },
    ]
    # gap = 0.95 - 0.4 = 0.55 > _CONFLICT_GAP (0.05) → no conflict
    with mock.patch("app.pipeline.resolve._score_and_rank", return_value=ranked):
        result = resolve_candidates({"company_name": "Acme Corp", "domain": "acme.com"})

    assert result["conflict_signal"] is False


# ---------------------------------------------------------------------------
# Pipeline stage contract tests
# ---------------------------------------------------------------------------


def test_stage_name():
    assert ResolveEntityCandidatesStage.name == "resolve_entity_candidates"


def test_stage_returns_context_with_candidates_key():
    """Stage adds context['candidates'] without removing existing keys."""
    stage = ResolveEntityCandidatesStage()
    context = {
        "normalized": {
            "company_name": "Acme Corporation",
            "domain": "acme.com",
        },
        "some_prior_key": "preserved",
    }

    # db is not used by this stage — pass a mock.
    result = stage.run("run-001", mock.MagicMock(), context)

    assert "candidates" in result
    assert "some_prior_key" in result
    assert result["some_prior_key"] == "preserved"


def test_stage_single_clear_submission():
    """Clear name + domain → single_match, high confidence, no conflict."""
    stage = ResolveEntityCandidatesStage()
    context = {
        "normalized": {
            "company_name": "Northwind Systems",
            "domain": "northwind.com",
        }
    }

    result = stage.run("run-001", mock.MagicMock(), context)
    candidates_ctx = result["candidates"]

    assert candidates_ctx["status"] == "single_match"
    assert candidates_ctx["conflict_signal"] is False
    assert candidates_ctx["top_candidate"] is not None
    assert candidates_ctx["top_candidate"]["overall_score"] >= _STRONG_MATCH_THRESHOLD


def test_stage_empty_normalized_returns_no_match():
    """Empty normalized context → no_match status."""
    stage = ResolveEntityCandidatesStage()
    result = stage.run("run-001", mock.MagicMock(), {"normalized": {}})
    assert result["candidates"]["status"] == "no_match"


def test_stage_conflict_signal_propagated():
    """Ambiguous candidates are flagged in the stage output."""
    close_score = _STRONG_MATCH_THRESHOLD - 0.1
    ranked = [
        {
            "name": "Acme Corp",
            "domain": "acme.com",
            "name_score": close_score,
            "domain_score": close_score,
            "overall_score": close_score,
        },
        {
            "name": "Acme Corporation",
            "domain": "acme.com",
            "name_score": close_score - (_CONFLICT_GAP / 2),
            "domain_score": close_score - (_CONFLICT_GAP / 2),
            "overall_score": close_score - (_CONFLICT_GAP / 2),
        },
    ]

    stage = ResolveEntityCandidatesStage()
    context = {"normalized": {"company_name": "Acme Corp", "domain": "acme.com"}}

    with mock.patch("app.pipeline.resolve._score_and_rank", return_value=ranked):
        result = stage.run("run-001", mock.MagicMock(), context)

    assert result["candidates"]["conflict_signal"] is True
