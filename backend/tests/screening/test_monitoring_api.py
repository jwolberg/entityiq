"""Monitoring alerts surfaced through the API (IS5-T2, ticket 0052)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.lists.persons import PersonRecord, store_records
from app.models.list_snapshot import ListSnapshot
from app.screening.monitor import rescreen_for_snapshot
from app.screening.pipeline import run_screening


def _snap(db, when, people):
    snap = ListSnapshot(
        source="ofac_sdn",
        retrieved_at=when,
        content_sha256=f"x{when}",
        record_count=len(people),
    )
    db.add(snap)
    db.flush()
    store_records(
        db,
        snap,
        [
            PersonRecord(
                source="ofac_sdn",
                source_entry_id=eid,
                primary_name=n,
                names=[{"name": n, "kind": "primary"}],
            )
            for eid, n in people
        ],
    )
    db.commit()
    return snap


def _monitoring_run(env):
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db = env["factory"]()
    _snap(db, t0, [("1", "Chidi Okafor")])
    db.close()
    first = (
        env["client"]
        .post(
            "/screenings",
            json={"name": "Nadia Bouhaddou"},
            headers=env["auth"]["operator"],
        )
        .json()["run_id"]
    )
    db = env["factory"]()
    new = _snap(
        db, t0 + timedelta(days=1), [("1", "Chidi Okafor"), ("2", "Nadia Bouhaddou")]
    )
    new_id = new.id
    created = rescreen_for_snapshot(
        db, new_id, enqueue=lambda rid: run_screening(rid, db)
    )
    db.close()
    return first, created[0], new_id


def test_queue_can_isolate_monitoring_alerts(api_env):
    first, monitored, _ = _monitoring_run(api_env)
    h = api_env["auth"]["operator"]
    items = (
        api_env["client"]
        .get("/screenings?trigger=monitoring", headers=h)
        .json()["items"]
    )
    assert [i["run_id"] for i in items] == [monitored]
    assert items[0]["trigger"] == "monitoring"
    intake = (
        api_env["client"].get("/screenings?trigger=intake", headers=h).json()["items"]
    )
    assert [i["run_id"] for i in intake] == [first]


def test_detail_shows_trigger_changed_records_and_prior_decision(api_env):
    first, monitored, snapshot_id = _monitoring_run(api_env)
    d = (
        api_env["client"]
        .get(f"/screenings/{monitored}", headers=api_env["auth"]["operator"])
        .json()
    )
    trig = d["monitoring"]
    assert trig["snapshot_id"] == snapshot_id
    assert trig["source"] == "ofac_sdn"
    assert trig["changed_entry_ids"] == ["2"]
    assert trig["prior_run_id"] == first
    assert trig["prior_disposition"] == "CLEAR"


def test_intake_runs_have_no_monitoring_block(api_env):
    first, _m, _s = _monitoring_run(api_env)
    d = (
        api_env["client"]
        .get(f"/screenings/{first}", headers=api_env["auth"]["operator"])
        .json()
    )
    assert d["monitoring"] is None
