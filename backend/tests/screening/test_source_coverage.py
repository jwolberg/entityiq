"""Per-list coverage and freshness gate auto-CLEAR (review finding #1; C2, F9, N4)."""

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
from app.screening.models import ScreeningDecision, ScreeningRun, ScreeningSubject
from app.screening.pipeline import run_screening

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_REQUIRED_SOURCES", "ofac_sdn,un_consolidated"
    )
    monkeypatch.setenv("ENTITYIQ_SCREENING_MAX_LIST_AGE_DAYS", "7")
    engine = create_engine(f"sqlite:///{tmp_path / 'cov.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _load(db, source, when):
    snap = ListSnapshot(
        source=source, retrieved_at=when, content_sha256=source, record_count=1
    )
    db.add(snap)
    db.flush()
    store_records(
        db,
        snap,
        [
            PersonRecord(
                source=source,
                source_entry_id="1",
                primary_name="Chidi Okafor",
                names=[{"name": "Chidi Okafor", "kind": "primary"}],
            )
        ],
    )
    db.commit()


def _screen(db):
    subject = ScreeningSubject()
    db.add(subject)
    db.flush()
    crypto.set_subject_pii(db, subject, {"name": "Ingrid Solheimsen"})
    run = ScreeningRun(subject_id=subject.id)
    db.add(run)
    db.commit()
    run_screening(run.id, db)
    db.expire_all()
    return db.get(ScreeningRun, run.id), db.query(ScreeningDecision).filter_by(
        run_id=run.id
    ).one()


def test_a_missing_required_list_blocks_auto_clear(db):
    _load(db, "ofac_sdn", NOW)  # UN never ingested
    run, decision = _screen(db)
    assert run.source_availability["list:ofac_sdn"] == "complete"
    assert run.source_availability["list:un_consolidated"] == "unavailable"
    assert decision.system_disposition == "REVIEW"
    assert decision.auto_closed is False


def test_a_stale_required_list_blocks_auto_clear(db):
    _load(db, "ofac_sdn", NOW)
    _load(db, "un_consolidated", NOW - timedelta(days=30))
    run, decision = _screen(db)
    assert run.source_availability["list:un_consolidated"] == "unavailable"
    assert decision.system_disposition == "REVIEW"


def test_all_required_lists_present_and_fresh_can_auto_clear(db):
    _load(db, "ofac_sdn", NOW)
    _load(db, "un_consolidated", NOW - timedelta(days=1))
    run, decision = _screen(db)
    assert decision.system_disposition == "CLEAR"
    assert decision.auto_closed is True
