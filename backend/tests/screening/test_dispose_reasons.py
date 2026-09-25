"""Band and run reasons from the disposition engine (ticket 0066).

``band_with_reason`` and ``run_reason`` are the single source of the banding
rules: ``decide()`` uses them, and the decision explanation (ticket 0067)
calls them on stored decisions. The golden fixture pins ``decide()`` output
for every corpus case so the refactor can't change a verdict.

Regenerate the fixture only for a deliberate verdict change:

    python -m tests.screening.test_dispose_reasons
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.screening.blocking import BlockingIndex
from app.screening.dispose import band_with_reason, decide, run_reason
from app.screening.eval import load_corpus
from app.screening.metrics import CORPUS_PATH
from app.screening.scoring import DEFAULT_RULE

GOLDEN = Path(__file__).parent / "fixtures" / "decide_corpus_golden.json"
THRESHOLDS = {"clear_below": 0.35, "match_at": 0.9}


def _term(name: str, weight: float) -> dict:
    return {"name": name, "weight": weight}


# ---------------------------------------------------------------------------
# band_with_reason: one test per reason code
# ---------------------------------------------------------------------------


def test_score_at_match_threshold_is_match():
    terms = [_term("name_exact_normalized", 0.5), _term("id_number_match", 0.6)]
    assert band_with_reason(1.1, terms, THRESHOLDS) == (
        "MATCH",
        "score_at_or_above_match",
    )


def test_score_exactly_at_match_threshold_is_match():
    assert band_with_reason(0.9, [], THRESHOLDS) == ("MATCH", "score_at_or_above_match")


def test_score_between_thresholds_is_review():
    terms = [_term("name_exact_normalized", 0.5)]
    assert band_with_reason(0.5, terms, THRESHOLDS) == ("REVIEW", "between_thresholds")


def test_score_exactly_at_clear_threshold_is_review():
    assert band_with_reason(0.35, [], THRESHOLDS) == ("REVIEW", "between_thresholds")


def test_low_score_without_conflict_is_clear():
    terms = [_term("name_partial_overlap", 0.2)]
    assert band_with_reason(0.2, terms, THRESHOLDS) == ("CLEAR", "score_below_clear")


@pytest.mark.parametrize(
    "conflict", ["dob_conflict", "dob_partial_conflict", "id_number_conflict"]
)
def test_name_match_with_conflict_is_floored_at_review(conflict):
    terms = [_term("name_exact_normalized", 0.5), _term(conflict, -0.35)]
    assert band_with_reason(0.15, terms, THRESHOLDS) == ("REVIEW", "conflict_floor")


def test_partial_name_overlap_with_conflict_is_not_floored():
    # name_partial_overlap is not one of the floor-eligible name terms.
    terms = [_term("name_partial_overlap", 0.2), _term("dob_conflict", -0.35)]
    assert band_with_reason(-0.15, terms, THRESHOLDS) == ("CLEAR", "score_below_clear")


# ---------------------------------------------------------------------------
# run_reason
# ---------------------------------------------------------------------------


def test_no_candidates_with_full_coverage_auto_clears():
    assert run_reason([], {"list:ofac_sdn": "complete"}) == (
        "CLEAR",
        True,
        "no_candidates",
    )


def test_no_candidates_with_a_coverage_gap_is_review():
    assert run_reason([], {"list:ofac_sdn": "unavailable"}) == (
        "REVIEW",
        False,
        "auto_clear_blocked_by_coverage",
    )


def test_all_clear_candidates_with_full_coverage_auto_clear():
    results = [{"band": "CLEAR"}, {"band": "CLEAR"}]
    assert run_reason(results, {"list:ofac_sdn": "complete"}) == (
        "CLEAR",
        True,
        "auto_clear_allowed",
    )


def test_all_clear_candidates_with_a_coverage_gap_is_review():
    results = [{"band": "CLEAR"}]
    assert run_reason(results, {"list:uk_ofsi": "unavailable"}) == (
        "REVIEW",
        False,
        "auto_clear_blocked_by_coverage",
    )


@pytest.mark.parametrize("worst", ["REVIEW", "MATCH"])
def test_run_takes_the_most_severe_band(worst):
    results = [{"band": "CLEAR"}, {"band": worst}, {"band": "CLEAR"}]
    assert run_reason(results, {}) == (worst, False, "rollup_most_severe")


def test_run_reason_agrees_with_decide_on_every_path():
    cases = [
        ([], {}),
        ([], {"list:x": "unavailable"}),
    ]
    for cands, status in cases:
        bundle = {
            "subject": {"name": "Nobody Listed"},
            "candidates": cands,
            "rule": DEFAULT_RULE,
            "normalizer_version": "n2",
            "source_status": status,
        }
        result = decide(bundle)
        disposition, auto_closed, _reason = run_reason(result["candidates"], status)
        assert (result["disposition"], result["auto_closed"]) == (
            disposition,
            auto_closed,
        )


# ---------------------------------------------------------------------------
# decide() output is unchanged for every corpus case
# ---------------------------------------------------------------------------


def corpus_outputs() -> dict[str, dict]:
    """decide() on every corpus case, with and without a coverage gap."""
    corpus = load_corpus(CORPUS_PATH)
    index = BlockingIndex.build(corpus.watchlist)
    by_id = {r["id"]: r for r in corpus.watchlist}
    out: dict[str, dict] = {}
    for case in corpus.cases:
        cands = [
            {"candidate_id": c.record_id, "record": by_id[c.record_id]}
            for c in index.candidates(case.subject["name"])
        ]
        for gap in (False, True):
            bundle = {
                "subject": case.subject,
                "candidates": cands,
                "rule": DEFAULT_RULE,
                "normalizer_version": "n2",
                "source_status": {
                    "list:ofac_sdn": "unavailable" if gap else "complete"
                },
            }
            out[case.id + ("-gap" if gap else "")] = decide(bundle)
    return out


def _golden(outputs: dict[str, dict]) -> dict:
    canonical = json.dumps(outputs, sort_keys=True, separators=(",", ":"))
    return {
        "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "cases": {
            key: {
                "disposition": r["disposition"],
                "auto_closed": r["auto_closed"],
                "top_score": r["top_score"],
                "candidates": [
                    [c["candidate_id"], c["score"], c["band"]] for c in r["candidates"]
                ],
            }
            for key, r in sorted(outputs.items())
        },
    }


def test_decide_output_is_byte_identical_to_the_golden_fixture():
    expected = json.loads(GOLDEN.read_text())
    actual = _golden(corpus_outputs())
    # Compare the readable summary first so a failure shows which case moved.
    assert actual["cases"] == expected["cases"]
    assert actual["sha256"] == expected["sha256"]


if __name__ == "__main__":  # pragma: no cover
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(json.dumps(_golden(corpus_outputs()), indent=1) + "\n")
    print(f"wrote {GOLDEN}")
