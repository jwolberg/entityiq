"""Evaluation harness for screening matchers (IS1-T6, ticket 0034).

Measures what the PRD makes a release gate (PRD-IDV §[7]):

  - blocking recall: the share of labeled true (subject, record) pairs that
    the blocker returns as candidates. Anything missed here is unrecoverable.
  - FP rate at 100% recall: with the threshold set to the lowest score any
    true hit receives, the share of non-hit candidates at or above it. Only
    defined when blocking recall is 100%; otherwise None.

Matchers are plain callables so any blocker or scorer can be measured:
``blocker(subject, records) -> iterable[record_id]`` and
``scorer(subject, record) -> float``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    subject: dict
    expected_hits: list[str]


@dataclass(frozen=True)
class Corpus:
    version: int
    watchlist: list[dict]
    cases: list[Case]


@dataclass
class EvalReport:
    blocking_recall: float
    missed: list[tuple[str, str]] = field(default_factory=list)
    candidates_per_case: float = 0.0
    fp_rate_at_full_recall: float | None = None
    threshold: float | None = None


def load_corpus(path: str | Path) -> Corpus:
    data = json.loads(Path(path).read_text())
    return Corpus(
        version=data["version"],
        watchlist=data["watchlist"],
        cases=[
            Case(c["id"], c["category"], c["subject"], list(c["expected_hits"]))
            for c in data["cases"]
        ],
    )


def evaluate(
    corpus: Corpus,
    blocker: Callable[[dict, list[dict]], Iterable[str]],
    scorer: Callable[[dict, dict], float] | None = None,
) -> EvalReport:
    by_id = {r["id"]: r for r in corpus.watchlist}
    true_pairs = 0
    found = 0
    missed: list[tuple[str, str]] = []
    total_candidates = 0
    hit_scores: list[float] = []
    other_scores: list[float] = []

    for case in corpus.cases:
        candidates = set(blocker(case.subject, corpus.watchlist))
        total_candidates += len(candidates)
        for hit in case.expected_hits:
            true_pairs += 1
            if hit in candidates:
                found += 1
            else:
                missed.append((case.id, hit))
        if scorer is not None:
            for rid in candidates:
                score = scorer(case.subject, by_id[rid])
                (hit_scores if rid in case.expected_hits else other_scores).append(
                    score
                )

    recall = found / true_pairs if true_pairs else 1.0
    report = EvalReport(
        blocking_recall=recall,
        missed=missed,
        candidates_per_case=total_candidates / max(len(corpus.cases), 1),
    )
    if scorer is not None and recall == 1.0 and hit_scores:
        threshold = min(hit_scores)
        flagged = sum(1 for s in other_scores if s >= threshold)
        report.threshold = threshold
        report.fp_rate_at_full_recall = (
            flagged / len(other_scores) if other_scores else 0.0
        )
    return report
