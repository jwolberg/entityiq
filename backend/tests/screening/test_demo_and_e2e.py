"""Screening demo data and end-to-end flow (IS2-T9, ticket 0044)."""

from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.screening import demo_data
from app.screening.models import ScreeningDecision
from tests.screening.conftest import load_people


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def test_demo_covers_every_disposition_and_is_idempotent(db):
    added = demo_data.load_screening_demo(db)
    assert added == len(demo_data.DEMO_SUBJECTS)

    outcomes = {
        (d.system_disposition, d.auto_closed) for d in db.query(ScreeningDecision)
    }
    assert ("MATCH", False) in outcomes
    assert ("REVIEW", False) in outcomes
    assert ("CLEAR", True) in outcomes

    assert demo_data.load_screening_demo(db) == 0
    assert db.query(ScreeningDecision).count() == added


def test_demo_uses_a_clearly_fictional_source():
    assert demo_data.DEMO_SOURCE == "demo_watchlist"
    names = {p["name"] for p in demo_data.DEMO_WATCHLIST}
    subjects = {s["name"] for s in demo_data.DEMO_SUBJECTS}
    assert names and subjects


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("sqlite:///x.db", True),
        ("postgresql+psycopg://u@h/db", False),
    ],
)
def test_demo_refuses_non_sqlite_without_explicit_opt_in(url, allowed, monkeypatch):
    monkeypatch.delenv("ENTITYIQ_ALLOW_DEMO_SEED", raising=False)
    if allowed:
        demo_data.check_demo_target(url)
    else:
        with pytest.raises(demo_data.UnsafeDemoTarget):
            demo_data.check_demo_target(url)
        monkeypatch.setenv("ENTITYIQ_ALLOW_DEMO_SEED", "1")
        demo_data.check_demo_target(url)


def test_end_to_end_submit_pipeline_disposition_replay(api_env, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("no network in the screening e2e")

    monkeypatch.setattr("httpx.get", no_network)
    load_people(
        api_env["factory"],
        [
            {
                "name": "Teodor Vasilescu",
                "dobs": [{"date": "1962-08-30"}],
                "nat": ["RO"],
            },
        ],
    )
    c, op, lead = (
        api_env["client"],
        api_env["auth"]["operator"],
        api_env["auth"]["lead"],
    )

    created = c.post(
        "/screenings",
        json={
            "name": "Teodor Vasilescu",
            "dob": "1962-08-30",
            "nationality": "Romania",
        },
        headers=op,
    ).json()
    assert created["disposition"] == "MATCH"
    run_id = created["run_id"]

    queue = c.get("/screenings", headers=op).json()["items"]
    assert queue[0]["run_id"] == run_id

    assert (
        c.post(
            f"/screenings/{run_id}/disposition",
            json={"disposition": "MATCH", "notes": "confirmed on passport"},
            headers=op,
        ).status_code
        == 201
    )
    detail = c.get(f"/screenings/{run_id}", headers=op).json()
    assert detail["dispositions"][-1]["disposition"] == "MATCH"

    replay = c.post(f"/screenings/{run_id}/replay", headers=lead).json()
    assert replay["reproduced"] is True
