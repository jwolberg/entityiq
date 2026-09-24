"""Person-name normalization and blocking with a recall gate (IS2-T2, 0037)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from app.screening.blocking import DEFAULT_CAP, BlockingIndex
from app.screening.eval import evaluate, load_corpus
from app.screening.names import NORMALIZER_VERSION, name_keys, tokens

CORPUS = load_corpus(Path(__file__).parent / "corpus" / "v1.json")


def _blocker(cap=DEFAULT_CAP):
    index = BlockingIndex.build(CORPUS.watchlist)

    def block(subject, _records):
        return [c.record_id for c in index.candidates(subject["name"], cap=cap)]

    return block


def test_blocking_recall_is_100_percent_on_the_corpus():
    """Release gate (PRD-IDV §[7]): CI fails if blocking misses any true hit."""
    report = evaluate(CORPUS, blocker=_blocker())
    assert report.missed == [], report.missed
    assert report.blocking_recall == 1.0


def test_recall_per_category():
    by_cat = defaultdict(list)
    for case in CORPUS.cases:
        by_cat[case.category].append(case)
    block = _blocker()
    for category, cases in by_cat.items():
        for case in cases:
            got = set(block(case.subject, CORPUS.watchlist))
            assert set(case.expected_hits) <= got, (category, case.id)


def test_cap_bounds_candidates_and_never_drops_a_true_hit():
    report = evaluate(CORPUS, blocker=_blocker(cap=10))
    assert report.blocking_recall == 1.0
    index = BlockingIndex.build(CORPUS.watchlist)
    assert all(
        len(index.candidates(c.subject["name"], cap=10)) <= 10 for c in CORPUS.cases
    )


def test_candidates_say_which_key_matched():
    index = BlockingIndex.build(CORPUS.watchlist)
    cands = index.candidates("Weiming Zhang", cap=DEFAULT_CAP)
    top = cands[0]
    assert top.matched_keys
    assert all(":" in k for k in top.matched_keys)


@pytest.mark.parametrize(
    "name",
    [
        "Дмитрий Короленко",
        "محمد الفاروقي",
        "Müller-Lüdenscheidt",
        "J. P. Wexfordham",
        "Saeed bin Rashid Al-Kuwaitri",
        "XiJinPing",
        "O'Brien-Smith",
        "   ",
    ],
)
def test_normalization_is_deterministic(name):
    assert name_keys(name) == name_keys(name)
    assert tokens(name) == tokens(name)


def test_particles_and_punctuation_are_dropped():
    assert tokens("Saeed bin Rashid Al-Kuwaitri") == ["saeed", "rashid", "kuwaitri"]
    assert tokens("J. P. Wexfordham") == ["j", "p", "wexfordham"]


def test_camel_case_output_from_cjk_transliteration_is_split():
    assert tokens("XiJinPing") == ["xi", "jin", "ping"]


def test_transliterations_share_a_key():
    assert name_keys("Dmitriy Korolenko") & name_keys("Дмитрий Короленко")
    assert name_keys("Husayn Tabakh") & name_keys("Hussein Tabbakh")


def test_unrelated_names_share_no_key():
    assert not name_keys("Ingrid Solheimsen") & name_keys("Chidi Okafor")


def test_normalizer_version_is_recorded():
    assert NORMALIZER_VERSION.startswith("n")


def test_cap_never_drops_a_record_matching_every_name_token():
    """Real lists have 440 'Mohammed Ali' full-name matches; a plain top-N cap
    would silently drop true candidates. Full-token matches are never capped;
    only partial matches are trimmed."""
    full = [
        {"id": f"F{i:03d}", "names": [{"name": "Ahmed Khan", "kind": "primary"}]}
        for i in range(250)
    ]
    partial = [
        {"id": f"P{i:03d}", "names": [{"name": f"Ahmed Q{i}xz", "kind": "primary"}]}
        for i in range(50)
    ]
    index = BlockingIndex.build(full + partial)

    got = [c.record_id for c in index.candidates("Ahmed Khan", cap=10)]

    assert {f"F{i:03d}" for i in range(250)} <= set(got)
    assert len([g for g in got if g.startswith("P")]) <= 10


def test_vowel_initial_skeleton_keeps_its_consonants():
    """Regression: the skeleton used to drop the first consonant after a
    leading vowel, reducing 'Iuliia'/'Yulia' to the near-useless key 'sk:i'."""
    assert "sk:il" in name_keys("Iuliia")
    assert "sk:il" in name_keys("Yulia")
    assert "sk:i" not in name_keys("Iuliia")


def test_nickname_true_hit_survives_a_large_surname_decoy_set():
    """Review finding #2: a nickname hit only shared the surname key, so it
    competed in the capped partial bucket and was dropped among decoys."""
    decoys = [
        {
            "id": f"D{i:04d}",
            "names": [{"name": f"Given{i} Harcourtney", "kind": "primary"}],
        }
        for i in range(500)
    ]
    hit = {"id": "HIT", "names": [{"name": "William Harcourtney", "kind": "primary"}]}
    index = BlockingIndex.build(decoys + [hit])
    got = [c.record_id for c in index.candidates("Bill Harcourtney")]
    assert "HIT" in got


def test_recall_holds_for_every_corpus_hit_among_realistic_decoy_volume():
    """Recall gate at list scale: 200 surname decoys per true hit (~16k)."""
    by_id = {r["id"]: r for r in CORPUS.watchlist}
    decoys = []
    for case in CORPUS.cases:
        for hit in case.expected_hits:
            surname = by_id[hit]["names"][0]["name"].split()[-1]
            decoys += [
                {
                    "id": f"{hit}-D{i}",
                    "names": [{"name": f"Zq{i}x {surname}", "kind": "primary"}],
                }
                for i in range(200)
            ]
    index = BlockingIndex.build(CORPUS.watchlist + decoys)
    for case in CORPUS.cases:
        got = {c.record_id for c in index.candidates(case.subject["name"])}
        assert set(case.expected_hits) <= got, case.id
