"""Blocking pipeline stage over stored list snapshots (IS2-T2, ticket 0037)."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.lists.persons import PersonRecord, store_records
from app.models.list_snapshot import ListSnapshot
from app.screening import crypto
from app.screening.models import ScreeningCandidate, ScreeningRun, ScreeningSubject
from app.screening.stages import BlockCandidatesStage, current_snapshot_ids


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _snapshot(db, source, when, people):
    snap = ListSnapshot(
        source=source,
        retrieved_at=when,
        content_sha256=source + str(when),
        record_count=len(people),
    )
    db.add(snap)
    db.flush()
    store_records(
        db,
        snap,
        [
            PersonRecord(
                source=source,
                source_entry_id=str(i),
                primary_name=n,
                names=[{"name": n, "kind": "primary"}],
            )
            for i, n in enumerate(people)
        ],
    )
    return snap


def _run(db, pii):
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    crypto.set_subject_pii(db, subject, pii)
    run = ScreeningRun(subject_id=subject.id)
    db.add(run)
    db.commit()
    return run


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_current_snapshots_are_the_latest_per_source(db):
    old = _snapshot(db, "ofac_sdn", T0, ["Old Person"])
    new = _snapshot(db, "ofac_sdn", T0 + timedelta(days=1), ["New Person"])
    un = _snapshot(db, "un_consolidated", T0, ["Un Person"])
    db.commit()
    assert set(current_snapshot_ids(db)) == {new.id, un.id}
    assert old.id not in current_snapshot_ids(db)


def test_stage_writes_candidates_with_matched_keys(db):
    _snapshot(db, "ofac_sdn", T0, ["Weiming ZHANG", "Unrelated Person"])
    db.commit()
    run = _run(db, {"name": "Zhang Weiming"})

    out = BlockCandidatesStage().run(run.id, db, {})

    rows = db.query(ScreeningCandidate).filter_by(run_id=run.id).all()
    assert len(rows) == 1
    assert rows[0].blocking_keys
    assert out["blocking"]["status"] == "complete"
    assert out["blocking"]["candidate_count"] == 1
    assert out["blocking"]["snapshot_ids"]


def test_aliases_and_original_script_names_are_also_blocked(db):
    _snapshot(db, "ofac_sdn", T0, ["Husayn Tabakh"])
    db.commit()
    run = _run(db, {"name": "Someone Else", "aliases": ["Hussein Tabbakh"]})

    BlockCandidatesStage().run(run.id, db, {})

    assert db.query(ScreeningCandidate).filter_by(run_id=run.id).count() == 1


def test_no_lists_loaded_is_unavailable_not_clear(db):
    run = _run(db, {"name": "Anyone At All"})
    out = BlockCandidatesStage().run(run.id, db, {})
    assert out["blocking"]["status"] == "unavailable"
    assert db.query(ScreeningCandidate).count() == 0
