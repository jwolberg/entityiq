"""screen_people stage: screen each officer/owner (ticket 0081).

Each person collected by collect_people is screened through the individual
screening pipeline, linked to the KYB entity. Names live only in the encrypted
screening subject; KYB evidence carries the disposition and a link.
"""

from __future__ import annotations

import base64
import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.session import Base
from app.models.audit_event import AuditEvent
from app.models.company_person import CompanyPerson
from app.models.entity import Entity
from app.models.evidence import Evidence
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.officer_screening.screen import ScreenPeopleStage
from app.screening import crypto
from app.screening.models import ScreeningRun, ScreeningSubject
from tests.screening.conftest import load_people

LISTED = {"name": "Teodor Vasilescu", "dobs": [{"date": "1962-08-30"}], "nat": ["RO"]}


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ENTITYIQ_SCREENING_MASTER_KEY", base64.b64encode(os.urandom(32)).decode()
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'people.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autocommit=False, autoflush=False)
    engine.dispose()


def _kyb_run(db, entity_id: str | None = None) -> VerificationRun:
    if entity_id is None:
        entity = Entity(canonical_name="Harbor Freight Lines")
        db.add(entity)
        db.flush()
        entity_id = entity.id
    sub = Submission(
        company_name="Harbor Freight Lines",
        domain="harbor.example",
        work_email="ops@harbor.example",
        country="GB",
        entity_id=entity_id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(submission_id=sub.id, entity_id=entity_id)
    db.add(run)
    db.commit()
    return run


def _person(name, *, relationships=("officer",), dob=None, nationality=None, roles=()):
    return {
        "name": name,
        "relationships": list(relationships),
        "roles": list(roles),
        "dob": dob,
        "nationality": nationality,
        "ownership_pct": None,
        "sources": [{"source": "declared", "locator": "submission"}],
    }


def _context(people, status="complete"):
    return {"people": {"status": status, "people": people}}


def test_screens_each_person_and_records_evidence_without_names(factory):
    load_people(factory, [LISTED])
    db = factory()
    run = _kyb_run(db)
    people = [
        _person(
            "Teodor Vasilescu",
            dob="1962-08-30",
            nationality="Romania",
            roles=["director"],
        ),
        _person("Ann Lee", relationships=("owner",)),
    ]

    out = ScreenPeopleStage().run(run.id, db, _context(people))

    assert out["screened_people"]["status"] == "complete"
    evidence = (
        db.query(Evidence)
        .filter_by(verification_run_id=run.id, source="officer_screening")
        .all()
    )
    by_disposition = {e.normalized_value: e for e in evidence}
    assert set(by_disposition) == {"MATCH", "CLEAR"}
    match = by_disposition["MATCH"]
    assert match.field == "officer_screening_result"
    assert match.tier == 1
    assert match.raw_payload["relationships"] == ["officer"]
    assert match.raw_payload["roles"] == ["director"]
    screening_run = db.get(ScreeningRun, match.raw_payload["screening_run_id"])
    assert screening_run.trigger == "kyb_officer"
    assert screening_run.status == "complete"
    # Names never land in KYB evidence.
    for ev in evidence:
        blob = json.dumps([ev.raw_payload, ev.attribution, ev.raw_value])
        assert "Vasilescu" not in blob and "Ann Lee" not in blob

    subjects = db.query(ScreeningSubject).all()
    assert {s.kyb_entity_id for s in subjects} == {run.entity_id}
    names = {crypto.get_subject_pii(db, s)["name"] for s in subjects}
    assert names == {"Teodor Vasilescu", "Ann Lee"}
    links = db.query(CompanyPerson).all()
    assert {(link.relationship, link.first_run_id) for link in links} == {
        ("officer", run.id),
        ("owner", run.id),
    }
    db.close()


def test_rerun_reuses_subjects_and_screens_again(factory):
    load_people(factory, [LISTED])
    db = factory()
    first = _kyb_run(db)
    people = [_person("Ann Lee"), _person("Bo Chen", relationships=("owner",))]
    ScreenPeopleStage().run(first.id, db, _context(people))

    second = _kyb_run(db, entity_id=first.entity_id)
    # Same people, different spelling/case: still the same subjects.
    again = [_person("ANN  LEE"), _person("Bo Chen", relationships=("owner",))]
    ScreenPeopleStage().run(second.id, db, _context(again))

    assert db.query(ScreeningSubject).count() == 2
    assert db.query(CompanyPerson).count() == 2
    assert db.query(ScreeningRun).filter_by(trigger="kyb_officer").count() == 4
    assert (
        db.query(Evidence)
        .filter_by(verification_run_id=second.id, source="officer_screening")
        .count()
        == 2
    )
    db.close()


def test_officer_and_owner_is_one_subject_two_links(factory):
    load_people(factory, [LISTED])
    db = factory()
    run = _kyb_run(db)
    both = _person("Ann Lee", relationships=("officer", "owner"))

    ScreenPeopleStage().run(run.id, db, _context([both]))

    assert db.query(ScreeningSubject).count() == 1
    assert db.query(ScreeningRun).count() == 1
    assert sorted(link.relationship for link in db.query(CompanyPerson)) == [
        "officer",
        "owner",
    ]
    [ev] = db.query(Evidence).filter_by(source="officer_screening").all()
    assert ev.raw_payload["relationships"] == ["officer", "owner"]
    db.close()


def test_each_screening_is_audited_without_pii(factory):
    load_people(factory, [LISTED])
    db = factory()
    run = _kyb_run(db)

    ScreenPeopleStage().run(run.id, db, _context([_person("Ann Lee")]))

    [event] = db.query(AuditEvent).filter_by(event_type="screening.submitted").all()
    assert event.payload["trigger"] == "kyb_officer"
    assert event.payload["verification_run_id"] == run.id
    assert "Ann Lee" not in json.dumps(event.payload)
    db.close()


def test_screening_not_configured_is_unavailable(factory, monkeypatch):
    monkeypatch.delenv("ENTITYIQ_SCREENING_MASTER_KEY")
    db = factory()
    run = _kyb_run(db)

    out = ScreenPeopleStage().run(run.id, db, _context([_person("Ann Lee")]))

    assert out["screened_people"]["status"] == "unavailable"
    assert db.query(ScreeningSubject).count() == 0
    assert db.query(Evidence).filter_by(source="officer_screening").count() == 0
    db.close()


def test_no_people_is_complete_and_writes_nothing(factory):
    db = factory()
    run = _kyb_run(db)

    out = ScreenPeopleStage().run(run.id, db, _context([]))

    assert out["screened_people"] == {"status": "complete", "count": 0}
    assert db.query(ScreeningRun).count() == 0
    db.close()
