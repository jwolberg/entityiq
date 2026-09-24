"""Screening metrics report (IS2-T10, ticket 0045; PRD-IDV §[7])."""

from __future__ import annotations

import json
from pathlib import Path

from app.screening import eval as harness
from app.screening import metrics


def test_report_has_all_headline_numbers(tmp_path):
    out = tmp_path / "m.json"
    report = metrics.build_report()
    metrics.write_report(report, out)

    data = json.loads(out.read_text())
    for key in (
        "blocking_recall",
        "fp_rate_at_full_recall",
        "abstention_rate",
        "coverage",
        "reproducibility",
        "cases",
        "per_category",
    ):
        assert key in data, key
    assert data["blocking_recall"] == 1.0
    assert data["reproducibility"] == 1.0
    assert 0.0 <= data["fp_rate_at_full_recall"] <= 1.0
    assert 0.0 <= data["abstention_rate"] <= 1.0
    assert set(data["per_category"]) == {
        "transliteration",
        "inversion",
        "patronymic",
        "nickname",
        "initials",
        "common_name_cluster",
        "partial_dob",
    }


def test_gate_passes_on_the_real_matcher():
    assert metrics.gate(metrics.build_report()) == []


def test_gate_fails_when_recall_or_reproducibility_drop():
    bad = {"blocking_recall": 0.98, "reproducibility": 0.99}
    assert {f["metric"] for f in metrics.gate(bad)} == {
        "blocking_recall",
        "reproducibility",
    }


def test_metrics_reuse_the_harness_not_a_reimplementation(monkeypatch):
    calls = []
    real = harness.evaluate

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(metrics, "evaluate", spy)
    metrics.build_report()
    assert calls


def test_cli_exit_code(tmp_path, monkeypatch):
    assert metrics.main([str(tmp_path / "ok.json")]) == 0
    monkeypatch.setattr(metrics, "gate", lambda r: [{"metric": "blocking_recall"}])
    assert metrics.main([str(tmp_path / "bad.json")]) == 1
    assert Path(tmp_path / "bad.json").exists()
