"""People behind a company run, and the queue side of it (ticket 0083).

GET /reports/{run_id}/people lists the officers/owners screened in a KYB run
with their dispositions. Names are decrypted for operator sessions only, and
every view is audited without them.
"""

from __future__ import annotations

import json

from app.models.audit_event import AuditEvent
from app.models.entity import Entity
from app.models.submission import Submission
from app.models.verification_run import VerificationRun
from app.officer_screening.screen import ScreenPeopleStage
from app.screening import crypto
from app.screening.models import ScreeningSubject
from tests.screening.conftest import load_people

LISTED = {"name": "Teodor Vasilescu", "dobs": [{"date": "1962-08-30"}], "nat": ["RO"]}


def _person(name, relationships=("officer",), roles=(), dob=None, nationality=None):
    return {
        "name": name,
        "relationships": list(relationships),
        "roles": list(roles),
        "dob": dob,
        "nationality": nationality,
        "ownership_pct": 60.0 if "owner" in relationships else None,
        "sources": [{"source": "declared", "locator": "submission"}],
    }


def _seed(factory, people, availability=None) -> tuple[str, str]:
    db = factory()
    entity = Entity(canonical_name="Harbor Freight Lines")
    db.add(entity)
    db.flush()
    sub = Submission(
        company_name="Harbor Freight Lines",
        domain="harbor.example",
        work_email="ops@harbor.example",
        country="GB",
        entity_id=entity.id,
    )
    db.add(sub)
    db.flush()
    run = VerificationRun(
        submission_id=sub.id,
        entity_id=entity.id,
        status="complete",
        source_availability=availability
        or {"collect_people": "unavailable", "screen_people": "complete"},
    )
    db.add(run)
    db.commit()
    ScreenPeopleStage().run(
        run.id, db, {"people": {"status": "complete", "people": people}}
    )
    ids = (run.id, entity.id)
    db.close()
    return ids


def _two_people(env):
    load_people(env["factory"], [LISTED])
    return _seed(
        env["factory"],
        [
            _person(
                "Teodor Vasilescu",
                roles=["director"],
                dob="1962-08-30",
                nationality="Romania",
            ),
            _person("Ann Lee", relationships=("owner",)),
        ],
    )


def test_operator_sees_people_with_names_and_dispositions(bridge_env):
    run_id, _ = _two_people(bridge_env)

    r = bridge_env["client"].get(
        f"/reports/{run_id}/people", headers=bridge_env["auth"]["operator"]
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == run_id
    assert body["sources"] == {"registry": "unavailable", "screening": "complete"}
    people = {p["name"]: p for p in body["people"]}
    assert set(people) == {"Teodor Vasilescu", "Ann Lee"}
    teodor = people["Teodor Vasilescu"]
    assert teodor["disposition"] == "MATCH"
    assert teodor["relationships"] == ["officer"]
    assert teodor["roles"] == ["director"]
    assert teodor["screening_run_id"]
    assert teodor["shredded"] is False
    assert teodor["human_disposition"] is None
    ann = people["Ann Lee"]
    assert (ann["disposition"], ann["ownership_pct"]) == ("CLEAR", 60.0)


def test_viewing_people_is_audited_without_names(bridge_env):
    run_id, _ = _two_people(bridge_env)

    bridge_env["client"].get(
        f"/reports/{run_id}/people", headers=bridge_env["auth"]["lead"]
    )

    db = bridge_env["factory"]()
    [event] = db.query(AuditEvent).filter_by(event_type="report.people_viewed").all()
    assert event.verification_run_id == run_id
    assert len(event.payload["screening_subject_ids"]) == 2
    blob = json.dumps(event.payload)
    assert "Vasilescu" not in blob and "Ann Lee" not in blob
    db.close()


def test_examiner_can_read_but_integration_keys_cannot(bridge_env):
    run_id, _ = _two_people(bridge_env)
    c = bridge_env["client"]

    assert (
        c.get(
            f"/reports/{run_id}/people", headers=bridge_env["auth"]["examiner"]
        ).status_code
        == 200
    )
    assert (
        c.get(f"/reports/{run_id}/people", headers=bridge_env["api_key"]).status_code
        == 401
    )


def test_shredded_person_shows_without_a_name(bridge_env):
    load_people(bridge_env["factory"], [LISTED])  # full coverage → CLEAR
    run_id, _ = _seed(bridge_env["factory"], [_person("Ann Lee")])
    db = bridge_env["factory"]()
    [subject] = db.query(ScreeningSubject).all()
    crypto.shred_subject(db, subject, crypto.utcnow())
    db.commit()
    db.close()

    r = bridge_env["client"].get(
        f"/reports/{run_id}/people", headers=bridge_env["auth"]["operator"]
    )

    [person] = r.json()["people"]
    assert (person["name"], person["shredded"]) == (None, True)
    assert person["disposition"] == "CLEAR"


def test_run_without_people_and_unknown_run(bridge_env):
    run_id, _ = _seed(
        bridge_env["factory"],
        [],
        availability={"collect_people": "complete", "screen_people": "complete"},
    )
    c, op = bridge_env["client"], bridge_env["auth"]["operator"]

    body = c.get(f"/reports/{run_id}/people", headers=op).json()
    assert body["people"] == []
    assert body["sources"] == {"registry": "complete", "screening": "complete"}
    assert c.get("/reports/nope/people", headers=op).status_code == 404


def test_company_link_for_an_officer_screening(bridge_env):
    run_id, entity_id = _two_people(bridge_env)

    r = bridge_env["client"].get(
        f"/officer-screening/entities/{entity_id}",
        headers=bridge_env["auth"]["operator"],
    )

    assert r.status_code == 200, r.text
    assert r.json() == {
        "entity_id": entity_id,
        "company_name": "Harbor Freight Lines",
        "latest_run_id": run_id,
    }


def test_queue_filters_officer_screenings_and_links_the_entity(bridge_env):
    _, entity_id = _two_people(bridge_env)
    c, op = bridge_env["client"], bridge_env["auth"]["operator"]
    c.post("/screenings", json={"name": "Walk In"}, headers=op)

    items = c.get("/screenings?trigger=kyb_officer", headers=op).json()["items"]

    assert {i["subject_name"] for i in items} == {"Teodor Vasilescu", "Ann Lee"}
    assert {i["kyb_entity_id"] for i in items} == {entity_id}
    assert {i["trigger"] for i in items} == {"kyb_officer"}
