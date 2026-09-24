"""Replay and reproducibility (IS2-T5, ticket 0040; PRD-IDV F16, N1)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models.audit_event import AuditEvent
from app.models.watchlist_record import WatchlistRecord
from app.screening.blocking import BlockingIndex
from app.screening.dispose import decide
from app.screening.eval import load_corpus
from app.screening.replay import compare_results
from app.screening.retention import shred_expired
from app.screening.scoring import DEFAULT_RULE
from tests.screening.conftest import load_people

CORPUS = load_corpus(Path(__file__).parent / "corpus" / "v2.json")
MATCH_SUBJECT = {"name": "Teodor Vasilescu", "dob": "1962-08-30", "nationality": "RO"}


def _match_run(env):
    load_people(
        env["factory"],
        [
            {
                "name": "Teodor Vasilescu",
                "dobs": [{"date": "1962-08-30"}],
                "nat": ["RO"],
            },
        ],
    )
    r = env["client"].post(
        "/screenings", json=MATCH_SUBJECT, headers=env["auth"]["operator"]
    )
    return r.json()["run_id"]


def test_replay_reproduces_the_original_verdict(api_env):
    run_id = _match_run(api_env)
    r = api_env["client"].post(
        f"/screenings/{run_id}/replay", headers=api_env["auth"]["lead"]
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reproduced"] is True
    assert body["original"]["disposition"] == body["replayed"]["disposition"] == "MATCH"
    assert body["differences"] == []


def test_replay_touches_no_lists_and_no_network(api_env, monkeypatch):
    run_id = _match_run(api_env)

    def boom(*a, **k):
        raise AssertionError("replay must not read lists or the network")

    monkeypatch.setattr("app.screening.stages.current_snapshot_ids", boom)
    monkeypatch.setattr(BlockingIndex, "build", classmethod(boom))
    monkeypatch.setattr("app.lists.ofac.default_fetcher", boom)
    monkeypatch.setattr("httpx.get", boom)

    r = api_env["client"].post(
        f"/screenings/{run_id}/replay", headers=api_env["auth"]["examiner"]
    )
    assert r.json()["reproduced"] is True


def test_replay_survives_the_list_changing_underneath(api_env):
    run_id = _match_run(api_env)
    db = api_env["factory"]()
    for rec in db.query(WatchlistRecord):
        rec.names = [{"name": "Somebody Else Entirely", "kind": "primary"}]
        rec.dobs = []
    db.commit()
    db.close()

    r = api_env["client"].post(
        f"/screenings/{run_id}/replay", headers=api_env["auth"]["lead"]
    )
    assert r.json()["reproduced"] is True
    assert r.json()["replayed"]["disposition"] == "MATCH"


def test_operators_cannot_replay(api_env):
    run_id = _match_run(api_env)
    r = api_env["client"].post(
        f"/screenings/{run_id}/replay", headers=api_env["auth"]["operator"]
    )
    assert r.status_code == 403


def test_replay_is_audited(api_env):
    run_id = _match_run(api_env)
    api_env["client"].post(
        f"/screenings/{run_id}/replay", headers=api_env["auth"]["lead"]
    )
    db = api_env["factory"]()
    ev = db.query(AuditEvent).filter_by(event_type="screening.replayed").one()
    assert ev.payload == {"run_id": run_id, "reproduced": True, "shredded": False}
    db.close()


def test_shredded_subject_reports_shredded_not_an_error(api_env):
    from app.screening.models import ScreeningRun, ScreeningSubject

    run_id = _match_run(api_env)
    db = api_env["factory"]()
    run = db.get(ScreeningRun, run_id)
    subject = db.get(ScreeningSubject, run.subject_id)
    subject.relationship_ended_at = datetime.now(timezone.utc) - timedelta(days=4000)
    db.commit()
    shred_expired(db)
    db.close()

    body = (
        api_env["client"]
        .post(f"/screenings/{run_id}/replay", headers=api_env["auth"]["lead"])
        .json()
    )
    assert body["shredded"] is True
    assert body["reproduced"] is None


def test_compare_results_names_every_difference():
    a = {
        "disposition": "MATCH",
        "auto_closed": False,
        "candidates": [{"candidate_id": "c1", "score": 0.9, "band": "MATCH"}],
    }
    b = {
        "disposition": "REVIEW",
        "auto_closed": False,
        "candidates": [{"candidate_id": "c1", "score": 0.6, "band": "REVIEW"}],
    }
    diffs = compare_results(a, b)
    assert {d["field"] for d in diffs} == {"disposition", "c1.score", "c1.band"}


def test_every_corpus_decision_reproduces_after_a_storage_round_trip():
    """CI gate (PRD-IDV §[7]): decision reproducibility must be 100%."""
    index = BlockingIndex.build(CORPUS.watchlist)
    by_id = {r["id"]: r for r in CORPUS.watchlist}
    for case in CORPUS.cases:
        bundle = {
            "subject": case.subject,
            "candidates": [
                {"candidate_id": c.record_id, "record": by_id[c.record_id]}
                for c in index.candidates(case.subject["name"])
            ],
            "rule": DEFAULT_RULE,
            "normalizer_version": "n1",
            "source_status": {"block_candidates": "complete"},
        }
        original = decide(bundle)
        replayed = decide(json.loads(json.dumps(bundle, sort_keys=True)))
        assert compare_results(original, replayed) == [], case.id
