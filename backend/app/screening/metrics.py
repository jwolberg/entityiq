"""Screening metrics over the adversarial corpus (IS2-T10, ticket 0045).

PRD-IDV §[7] headline numbers, computed with the same harness, blocker,
scorer and ``decide`` the product uses:

  blocking_recall         labeled true pairs returned by blocking (gate: 1.0)
  fp_rate_at_full_recall  non-hit candidates scoring at/above the lowest true hit
  abstention_rate         share of cases the engine sends to REVIEW
  coverage                share of decisions where every source answered
  reproducibility         share of decisions identical after a storage round-trip
                          (gate: 1.0)

    python -m app.screening.metrics screening-metrics.json

Writes the JSON report and exits 1 if a gated metric misses its target. CI
uploads the report as an artifact.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from app.screening.blocking import BlockingIndex
from app.screening.dispose import decide
from app.screening.eval import evaluate, load_corpus
from app.screening.names import NORMALIZER_VERSION
from app.screening.replay import compare_results
from app.screening.scoring import DEFAULT_RULE, score_pair

CORPUS_PATH = Path(__file__).resolve().parents[2] / "tests/screening/corpus/v2.json"
GATES = {"blocking_recall": 1.0, "reproducibility": 1.0}


def build_report(corpus_path: Path = CORPUS_PATH, rule: dict = DEFAULT_RULE) -> dict:
    corpus = load_corpus(corpus_path)
    index = BlockingIndex.build(corpus.watchlist)
    by_id = {r["id"]: r for r in corpus.watchlist}

    def blocker(subject, _records):
        return [c.record_id for c in index.candidates(subject["name"])]

    def scorer(subject, record):
        return score_pair(subject, record, rule)[0]

    overall = evaluate(corpus, blocker=blocker, scorer=scorer)

    review = reproduced = covered = 0
    per_category: dict[str, dict] = defaultdict(
        lambda: {"cases": 0, "review": 0, "dispositions": Counter()}
    )
    for case in corpus.cases:
        bundle = {
            "subject": case.subject,
            "candidates": [
                {"candidate_id": rid, "record": by_id[rid]}
                for rid in blocker(case.subject, corpus.watchlist)
            ],
            "rule": rule,
            "normalizer_version": NORMALIZER_VERSION,
            "source_status": {"block_candidates": "complete"},
        }
        result = decide(bundle)
        again = decide(json.loads(json.dumps(bundle, sort_keys=True)))
        reproduced += not compare_results(result, again)
        covered += all(v != "unavailable" for v in bundle["source_status"].values())
        is_review = result["disposition"] == "REVIEW"
        review += is_review
        per_category[case.category]["cases"] += 1
        per_category[case.category]["review"] += is_review
        per_category[case.category]["dispositions"][result["disposition"]] += 1

    n = len(corpus.cases)
    return {
        "corpus_version": corpus.version,
        "rule_version": rule.get("version"),
        "normalizer_version": NORMALIZER_VERSION,
        "cases": n,
        "blocking_recall": overall.blocking_recall,
        "missed": overall.missed,
        "candidates_per_case": round(overall.candidates_per_case, 3),
        "fp_rate_at_full_recall": overall.fp_rate_at_full_recall,
        "threshold_at_full_recall": overall.threshold,
        "abstention_rate": round(review / n, 4) if n else 0.0,
        "coverage": round(covered / n, 4) if n else 1.0,
        "reproducibility": round(reproduced / n, 4) if n else 1.0,
        "per_category": {
            cat: {
                **v,
                "dispositions": dict(sorted(v["dispositions"].items())),
                "abstention_rate": round(v["review"] / v["cases"], 4),
            }
            for cat, v in sorted(per_category.items())
        },
    }


def gate(report: dict) -> list[dict]:
    return [
        {"metric": m, "value": report.get(m), "target": target}
        for m, target in GATES.items()
        if (report.get(m) or 0) < target
    ]


def write_report(report: dict, path: Path) -> None:
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[0]) if argv else Path("screening-metrics.json")
    report = build_report()
    write_report(report, out)
    failures = gate(report)
    for f in failures:
        print(
            f"GATE FAILED: {f['metric']} = {f.get('value')} "
            f"(target {f.get('target')})"
        )
    print(
        f"recall={report['blocking_recall']} "
        f"fp@100%recall={report['fp_rate_at_full_recall']} "
        f"abstention={report['abstention_rate']} "
        f"reproducibility={report['reproducibility']} -> {out}"
    )
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
