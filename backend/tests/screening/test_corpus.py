"""Adversarial name corpus and evaluation harness (IS1-T6, ticket 0034)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.screening.eval import evaluate, load_corpus
from tests.screening.corpus import generate

CORPUS = Path(__file__).parent / "corpus" / "v2.json"
CATEGORIES = {
    "transliteration",
    "inversion",
    "patronymic",
    "nickname",
    "initials",
    "common_name_cluster",
    "partial_dob",
    "corroborated_match",
    "clean",
}


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(CORPUS)


def test_checked_in_corpus_matches_the_generator():
    """Regenerate with: python -m tests.screening.corpus.generate"""
    assert json.loads(CORPUS.read_text()) == generate.build()


def test_every_category_has_at_least_ten_labeled_cases(corpus):
    counts = Counter(c.category for c in corpus.cases)
    assert set(counts) == CATEGORIES
    assert all(n >= 10 for n in counts.values()), counts
    assert all(c.expected_hits is not None for c in corpus.cases)


def test_corpus_has_true_hits_and_true_negatives(corpus):
    assert any(c.expected_hits for c in corpus.cases)
    assert any(not c.expected_hits for c in corpus.cases)


def test_labels_reference_real_watchlist_ids(corpus):
    ids = {r["id"] for r in corpus.watchlist}
    for case in corpus.cases:
        assert set(case.expected_hits) <= ids, case.id


def test_corpus_is_fictional_and_disjoint_from_parser_fixtures(corpus):
    """No corpus name reuses a name from the list-parser fixtures."""
    fixture_text = (
        Path(__file__).parents[1] / "lists" / "test_person_parsers.py"
    ).read_text()
    for record in corpus.watchlist:
        for name in record["names"]:
            assert name["name"] not in fixture_text, name["name"]


# ---------------------------------------------------------------------------
# Harness behavior with trivial matchers
# ---------------------------------------------------------------------------


def _all_records(subject, records):
    return [r["id"] for r in records]


def _exact_name(subject, records):
    return [
        r["id"]
        for r in records
        if any(n["name"].lower() == subject["name"].lower() for n in r["names"])
    ]


def test_blocking_everything_has_full_recall(corpus):
    report = evaluate(corpus, blocker=_all_records)
    assert report.blocking_recall == 1.0
    assert report.missed == []


def test_exact_matching_misses_adversarial_variants(corpus):
    report = evaluate(corpus, blocker=_exact_name)
    assert report.blocking_recall < 0.5
    assert report.missed  # names the (case, record) pairs it lost


def test_perfect_scorer_has_zero_false_positives_at_full_recall(corpus):
    truth = {(c.id, h) for c in corpus.cases for h in c.expected_hits}
    by_subject = {id(c.subject): c.id for c in corpus.cases}

    def perfect(subject, record):
        return 1.0 if (by_subject[id(subject)], record["id"]) in truth else 0.0

    report = evaluate(corpus, blocker=_all_records, scorer=perfect)
    assert report.fp_rate_at_full_recall == 0.0


def test_constant_scorer_flags_every_candidate(corpus):
    report = evaluate(corpus, blocker=_all_records, scorer=lambda s, r: 0.5)
    assert report.fp_rate_at_full_recall == 1.0


def test_fp_rate_is_none_when_recall_is_not_full(corpus):
    report = evaluate(corpus, blocker=_exact_name, scorer=lambda s, r: 1.0)
    assert report.fp_rate_at_full_recall is None
