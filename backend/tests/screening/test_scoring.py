"""Screening scoring terms and versioned rule config (IS2-T3, ticket 0038)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.screening.eval import load_corpus
from app.screening.scoring import (
    DEFAULT_RULE,
    TERM_CATALOG,
    compare_names,
    score_pair,
)

CORPUS = load_corpus(Path(__file__).parent / "corpus" / "v2.json")


def _rec(name, *, dobs=(), nat=(), docs=(), aliases=(), pobs=()):
    return {
        "id": "R1",
        "names": [{"name": name, "kind": "primary"}]
        + [{"name": a, "kind": "aka"} for a in aliases],
        "dobs": list(dobs),
        "nationalities": list(nat),
        "documents": list(docs),
        "pobs": list(pobs),
    }


def _terms(subject, record, **kw):
    _score, terms = score_pair(subject, record, DEFAULT_RULE, **kw)
    return {t["name"]: t for t in terms}


# ---------------------------------------------------------------------------
# Name terms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "record", "expected"),
    [
        ("Weiming Zhang", "Weiming Zhang", "name_exact_normalized"),
        ("Weiming Zhang", "ZHANG Weiming", "name_token_reordered"),
        ("Dmitriy Korolenko", "Dmitri Korolenko", "name_translit_equivalent"),
        ("Bill Harcourtney", "William Harcourtney", "name_translit_equivalent"),
        ("Viktor Morozko", "Viktor Andreyevich Morozko", "name_translit_equivalent"),
        ("J. P. Wexfordham", "John Patrick Wexfordham", "name_initials_compatible"),
        ("Ahmed Morozko", "Viktor Morozko", "name_partial_overlap"),
    ],
)
def test_best_name_term(subject, record, expected):
    assert compare_names(subject, [{"name": record}])["name"] == expected


def test_every_catalog_term_has_a_weight_in_the_default_rule():
    assert set(TERM_CATALOG) <= set(DEFAULT_RULE["weights"])


# ---------------------------------------------------------------------------
# Corroboration (F8), conflicts (F10), citations (F7)
# ---------------------------------------------------------------------------


def test_id_or_full_dob_outranks_surname_only_similarity():
    surname_only = score_pair(
        {"name": "Ahmed Morozko"}, _rec("Viktor Morozko"), DEFAULT_RULE
    )[0]
    with_dob = score_pair(
        {"name": "Ahmed Morozko", "dob": "1970-01-02"},
        _rec("Viktor Morozko", dobs=[{"date": "1970-01-02"}]),
        DEFAULT_RULE,
    )[0]
    with_id = score_pair(
        {
            "name": "Ahmed Morozko",
            "documents": [{"type": "passport", "number": "X-123", "country": "UT"}],
        },
        _rec(
            "Viktor Morozko",
            docs=[{"type": "passport", "number": "x123", "country": "UT"}],
        ),
        DEFAULT_RULE,
    )[0]
    assert with_dob > surname_only
    assert with_id > with_dob


def test_dob_terms():
    subj = {"name": "Teodor Vasilescu", "dob": "1962-08-30"}
    assert "dob_full_match" in _terms(
        subj, _rec("Teodor Vasilescu", dobs=[{"date": "1962-08-30"}])
    )
    assert "dob_year_match" in _terms(
        subj, _rec("Teodor Vasilescu", dobs=[{"year": 1962}])
    )
    assert "dob_within_range" in _terms(
        subj, _rec("Teodor Vasilescu", dobs=[{"from_year": 1960, "to_year": 1964}])
    )
    assert "dob_within_range" in _terms(
        subj, _rec("Teodor Vasilescu", dobs=[{"year": 1963, "circa": True}])
    )
    assert "dob_conflict" in _terms(
        subj, _rec("Teodor Vasilescu", dobs=[{"date": "1971-01-01"}])
    )
    assert "dob_conflict" in _terms(
        subj, _rec("Teodor Vasilescu", dobs=[{"year": 1975}])
    )


def test_no_dob_on_either_side_adds_no_dob_term():
    terms = _terms({"name": "Teodor Vasilescu"}, _rec("Teodor Vasilescu"))
    assert not any(n.startswith("dob_") for n in terms)


def test_conflict_is_surfaced_alongside_the_match_not_netted_away():
    terms = _terms(
        {
            "name": "Teodor Vasilescu",
            "dob": "1962-08-30",
            "documents": [{"type": "passport", "number": "A1", "country": "RO"}],
        },
        _rec(
            "Teodor Vasilescu",
            dobs=[{"date": "1970-01-01"}],
            docs=[{"type": "passport", "number": "B2", "country": "RO"}],
        ),
    )
    assert {"name_exact_normalized", "dob_conflict", "id_number_conflict"} <= set(terms)


def test_nationality_and_pob():
    terms = _terms(
        {"name": "Teodor Vasilescu", "nationality": "Romania", "pob": "Brasov"},
        _rec("Teodor Vasilescu", nat=["RO"], pobs=["Brasov, Romania"]),
    )
    assert "nationality_match" in terms and "pob_match" in terms
    terms = _terms(
        {"name": "Teodor Vasilescu", "nationality": "Germany"},
        _rec("Teodor Vasilescu", nat=["Romania"]),
    )
    assert "nationality_conflict" in terms


def test_common_name_penalty_applies_without_corroboration():
    plain = _terms({"name": "Ahmed Khan"}, _rec("Ahmed Khan"), name_frequency=50)
    assert "common_name_penalty" in plain
    corroborated = _terms(
        {"name": "Ahmed Khan", "dob": "1978-01-11"},
        _rec("Ahmed Khan", dobs=[{"date": "1978-01-11"}]),
        name_frequency=50,
    )
    assert "common_name_penalty" not in corroborated


def test_every_term_cites_both_sides():
    _s, terms = score_pair(
        {"name": "Teodor Vasilescu", "dob": "1962-08-30"},
        _rec("Teodor Vasilescu", dobs=[{"date": "1962-08-30"}]),
        DEFAULT_RULE,
    )
    for term in terms:
        assert term["subject_field"] and term["record_field"], term
        assert term["weight"] == DEFAULT_RULE["weights"][term["name"]]


def test_scores_are_deterministic():
    args = (
        {"name": "Dmitriy Korolenko", "dob": "1969-02-11"},
        _rec("Dmitri Korolenko", dobs=[{"date": "1969-02-11"}]),
        DEFAULT_RULE,
    )
    assert score_pair(*args) == score_pair(*args)


# ---------------------------------------------------------------------------
# Corpus gate: no true hit may fall below the CLEAR threshold
# ---------------------------------------------------------------------------


def test_no_true_hit_scores_below_the_clear_threshold():
    """Guarded auto-CLEAR (C2) is only safe if true hits never score as CLEAR."""
    by_id = {r["id"]: r for r in CORPUS.watchlist}
    clear_below = DEFAULT_RULE["thresholds"]["clear_below"]
    for case in CORPUS.cases:
        for hit in case.expected_hits:
            score, terms = score_pair(case.subject, by_id[hit], DEFAULT_RULE)
            assert score >= clear_below, (case.id, case.category, score, terms)
