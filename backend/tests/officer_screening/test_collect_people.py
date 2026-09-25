"""collect_people stage: declared + registry officers/owners (ticket 0080).

Turns the submitter's declared people and the registry's officers/owners into
one typed, de-duplicated list, each person citing every source that named
them. Nothing is persisted here; screen_people (ticket 0081) does that.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base
from app.models.entity import Entity
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.officer_screening.people import (
    CollectPeopleStage,
    StubRegistryPeopleProvider,
    UnconfiguredRegistryPeopleProvider,
    registry_people_provider_from_env,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sess = Session(bind=engine)
    yield sess
    sess.close()
    engine.dispose()


def _run(db, declared=None) -> str:
    entity = Entity(canonical_name="Harbor Freight Lines")
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Harbor Freight Lines",
        domain="harbor.example",
        work_email="ops@harbor.example",
        country="GB",
        entity_id=entity.id,
        declared_people=declared,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(submission_id=sub.id, entity_id=entity.id)
    db.add(run)
    db.commit()
    return run.id


_CONTEXT = {"normalized": {"company_name": "Harbor Freight Lines", "country_iso": "GB"}}

_REGISTRY = {
    "harbor freight lines": [
        {
            "name": "JOSÉ ÁLVAREZ",
            "relationship": "officer",
            "role": "director",
            "locator": "stub:harbor/officers/1",
        },
        {
            "name": "Mara Quint",
            "relationship": "owner",
            "ownership_pct": 75,
            "dob": "1968-02",
            "nationality": "MT",
            "locator": "stub:harbor/psc/1",
        },
    ]
}


def test_declared_only_when_registry_not_configured(db):
    run_id = _run(db, [{"name": "Ann Lee", "relationship": "officer", "role": "CFO"}])
    stage = CollectPeopleStage(provider=UnconfiguredRegistryPeopleProvider())

    out = stage.run(run_id, db, dict(_CONTEXT))

    people = out["people"]
    assert people["status"] == "unavailable"  # registry part: a coverage gap
    assert [p["name"] for p in people["people"]] == ["Ann Lee"]
    [ann] = people["people"]
    assert ann["relationships"] == ["officer"]
    assert ann["roles"] == ["CFO"]
    assert ann["sources"] == [{"source": "declared", "locator": "submission"}]


def test_declared_and_registry_merge_on_normalized_name(db):
    run_id = _run(
        db,
        [
            {"name": "Jose Alvarez", "relationship": "owner", "ownership_pct": 40},
            {"name": "Ann Lee", "relationship": "officer"},
        ],
    )
    stage = CollectPeopleStage(provider=StubRegistryPeopleProvider(_REGISTRY))

    out = stage.run(run_id, db, dict(_CONTEXT))

    people = {p["name"]: p for p in out["people"]["people"]}
    assert out["people"]["status"] == "complete"
    assert set(people) == {"Jose Alvarez", "Ann Lee", "Mara Quint"}
    jose = people["Jose Alvarez"]  # declared spelling wins; registry cited too
    assert jose["relationships"] == ["officer", "owner"]
    assert jose["roles"] == ["director"]
    assert jose["ownership_pct"] == 40
    assert jose["sources"] == [
        {"source": "declared", "locator": "submission"},
        {"source": "registry", "provider": "stub", "locator": "stub:harbor/officers/1"},
    ]
    mara = people["Mara Quint"]
    assert (mara["dob"], mara["nationality"], mara["ownership_pct"]) == (
        "1968-02",
        "MT",
        75,
    )


def test_conflicting_dates_of_birth_are_different_people(db):
    run_id = _run(
        db, [{"name": "Mara Quint", "relationship": "officer", "dob": "1990"}]
    )
    stage = CollectPeopleStage(provider=StubRegistryPeopleProvider(_REGISTRY))

    out = stage.run(run_id, db, dict(_CONTEXT))

    maras = [p for p in out["people"]["people"] if p["name"] == "Mara Quint"]
    assert sorted(p["dob"] for p in maras) == ["1968-02", "1990"]


def test_no_people_anywhere_is_complete_and_empty(db):
    run_id = _run(db)
    stage = CollectPeopleStage(provider=StubRegistryPeopleProvider({}))

    out = stage.run(run_id, db, dict(_CONTEXT))

    assert out["people"] == {"status": "complete", "people": []}


def test_provider_error_keeps_declared_people(db):
    class Broken:
        name = "broken"

        def lookup(self, company_name, country_iso):
            raise RuntimeError("registry exploded")

    run_id = _run(db, [{"name": "Ann Lee", "relationship": "officer"}])

    out = CollectPeopleStage(provider=Broken()).run(run_id, db, dict(_CONTEXT))

    assert out["people"]["status"] == "unavailable"
    assert [p["name"] for p in out["people"]["people"]] == ["Ann Lee"]


def test_provider_from_env(monkeypatch):
    monkeypatch.delenv("ENTITYIQ_REGISTRY_PEOPLE_PROVIDER", raising=False)
    assert isinstance(
        registry_people_provider_from_env(), UnconfiguredRegistryPeopleProvider
    )
    monkeypatch.setenv("ENTITYIQ_REGISTRY_PEOPLE_PROVIDER", "stub")
    assert isinstance(registry_people_provider_from_env(), StubRegistryPeopleProvider)
    monkeypatch.setenv("ENTITYIQ_REGISTRY_PEOPLE_PROVIDER", "nope")
    with pytest.raises(ValueError):
        registry_people_provider_from_env()
