"""List-delta monitoring and re-screening (IS5-T1, ticket 0051; PRD-IDV F17)."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.session import Base
from app.lists.delta import diff_snapshots
from app.lists.persons import PersonRecord, store_records
from app.models.audit_event import AuditEvent
from app.models.list_snapshot import ListSnapshot
from app.screening import crypto
from app.screening.models import ScreeningDecision, ScreeningRun, ScreeningSubject
from app.screening.monitor import rescreen_for_snapshot
from app.screening.pipeline import run_screening

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'mon.db'}")
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _snap(db, when, people, source="ofac_sdn"):
    snap = ListSnapshot(
        source=source,
        retrieved_at=when,
        content_sha256=f"{source}{when}",
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
                source_entry_id=eid,
                primary_name=name,
                names=[{"name": name, "kind": "primary"}],
                dobs=dobs,
            )
            for eid, name, dobs in people
        ],
    )
    db.commit()
    return snap


def _screened(db, pii, **subject_fields):
    subject = ScreeningSubject(**subject_fields)
    db.add(subject)
    db.flush()
    crypto.set_subject_pii(db, subject, pii)
    run = ScreeningRun(subject_id=subject.id)
    db.add(run)
    db.commit()
    run_screening(run.id, db)
    return subject, run


def test_diff_classifies_added_changed_removed(db):
    old = _snap(
        db,
        T0,
        [
            ("1", "Teodor Vasilescu", []),
            ("2", "Helena Lindqvistad", []),
            ("3", "Pavel Dvorakek", []),
        ],
    )
    new = _snap(
        db,
        T0 + timedelta(days=1),
        [
            ("1", "Teodor Vasilescu", []),
            ("2", "Helena Lindqvistad", [{"year": 1970}]),
            ("4", "Nadia Bouhaddou", []),
        ],
    )
    delta = diff_snapshots(db, old.id, new.id)
    assert delta.added_entry_ids == ["4"]
    assert delta.changed_entry_ids == ["2"]
    assert delta.removed_entry_ids == ["3"]


def test_only_subjects_hit_by_the_delta_are_rescreened(db):
    old = _snap(db, T0, [("1", "Chidi Okafor", [])])
    hit, first_run = _screened(db, {"name": "Nadia Bouhaddou"})
    untouched, _ = _screened(db, {"name": "Ingrid Solheimsen"})
    new = _snap(
        db,
        T0 + timedelta(days=1),
        [
            ("1", "Chidi Okafor", []),
            ("2", "Nadia Bouhaddou", []),
        ],
    )

    created = rescreen_for_snapshot(
        db, new.id, enqueue=lambda rid: run_screening(rid, db)
    )

    assert len(created) == 1
    run = db.get(ScreeningRun, created[0])
    assert run.subject_id == hit.id
    assert run.trigger == "monitoring"
    assert run.triggered_by_snapshot_id == new.id
    assert run.prior_run_id == first_run.id
    assert db.query(ScreeningRun).filter_by(subject_id=untouched.id).count() == 1
    assert old.id != new.id


def test_rescreen_creates_new_decisions_and_leaves_old_ones_alone(db):
    _snap(db, T0, [("1", "Chidi Okafor", [])])
    subject, first_run = _screened(db, {"name": "Nadia Bouhaddou"})
    before = db.query(ScreeningDecision).filter_by(run_id=first_run.id).one()
    before_state = (before.system_disposition, before.terms, before.frozen_ciphertext)
    new = _snap(
        db,
        T0 + timedelta(days=1),
        [("1", "Chidi Okafor", []), ("2", "Nadia Bouhaddou", [])],
    )

    created = rescreen_for_snapshot(
        db, new.id, enqueue=lambda rid: run_screening(rid, db)
    )

    db.expire_all()
    after_first = db.get(ScreeningDecision, before.id)
    assert (
        after_first.system_disposition,
        after_first.terms,
        after_first.frozen_ciphertext,
    ) == before_state
    assert before_state[0] == "CLEAR"
    new_decision = db.query(ScreeningDecision).filter_by(run_id=created[0]).one()
    assert new_decision.system_disposition == "REVIEW"


def test_shredded_and_ended_relationships_are_skipped(db):
    _snap(db, T0, [])
    shredded, _ = _screened(db, {"name": "Nadia Bouhaddou"})
    crypto.shred_subject(db, shredded, datetime.now(timezone.utc))
    db.commit()
    ended, _ = _screened(
        db,
        {"name": "Nadia Bouhaddou"},
        relationship_ended_at=datetime.now(timezone.utc),
    )
    new = _snap(db, T0 + timedelta(days=1), [("2", "Nadia Bouhaddou", [])])

    assert rescreen_for_snapshot(db, new.id, enqueue=lambda rid: None) == []


def test_first_snapshot_of_a_source_rescreens_against_everything(db):
    subject, _ = _screened(db, {"name": "Nadia Bouhaddou"})
    first = _snap(db, T0, [("2", "Nadia Bouhaddou", [])], source="un_consolidated")
    created = rescreen_for_snapshot(db, first.id, enqueue=lambda rid: None)
    assert len(created) == 1


def test_rescreening_is_audited_with_counts(db):
    _snap(db, T0, [])
    _screened(db, {"name": "Nadia Bouhaddou"})
    new = _snap(db, T0 + timedelta(days=1), [("2", "Nadia Bouhaddou", [])])
    rescreen_for_snapshot(db, new.id, enqueue=lambda rid: None)
    ev = (
        db.query(AuditEvent).filter_by(event_type="screening.monitoring_rescreen").one()
    )
    assert ev.payload["rescreened"] == 1
    assert ev.payload["snapshot_id"] == new.id
    assert ev.payload["delta"] == {"added": 1, "changed": 0, "removed": 0}
