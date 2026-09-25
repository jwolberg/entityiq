"""Screening intake API — POST /screenings (IS2-T1, ticket 0036)."""

from __future__ import annotations

import pytest

from app.models.audit_event import AuditEvent
from app.screening import crypto
from app.screening.models import ScreeningRun, ScreeningSubject
from tests.screening.conftest import load_people


def test_operator_can_submit_name_only(api_env):
    r = api_env["client"].post(
        "/screenings",
        json={"name": "Ingrid Solheimsen"},
        headers=api_env["auth"]["operator"],
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["run_id"]
    # No lists loaded → the blocking source is unavailable → REVIEW, never CLEAR.
    assert body["disposition"] == "REVIEW"
    assert body["auto_closed"] is False


def test_api_key_can_submit_with_all_attributes(api_env):
    load_people(api_env["factory"], [{"name": "Chidi Okafor"}])
    payload = {
        "name": "Ingrid Solheimsen",
        "original_script_name": "Ингрид Сольхеймсен",
        "aliases": ["Inga Solheim"],
        "dob": "1984-03",
        "nationality": "Norway",
        "country_of_residence": "NO",
        "pob": "Bergen",
        "gender": "female",
        "documents": [{"type": "passport", "number": "NO1234567", "country": "NO"}],
        "address": "1 Fjord Road, Bergen",
    }
    r = api_env["client"].post("/screenings", json=payload, headers=api_env["api_key"])
    assert r.status_code == 201, r.text
    assert r.json()["disposition"] == "CLEAR"
    assert r.json()["auto_closed"] is True

    db = api_env["factory"]()
    run = db.get(ScreeningRun, r.json()["run_id"])
    subject = db.get(ScreeningSubject, run.subject_id)
    assert subject.api_client_id == api_env["api_client_id"]
    stored = crypto.get_subject_pii(db, subject)
    assert stored["name"] == "Ingrid Solheimsen"
    assert stored["documents"][0]["number"] == "NO1234567"
    db.close()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "   "},
        {"name": "X Y", "dob": "30/08/1962"},
        {"name": "X Y", "dob": "1962-13-01"},
        {"name": "X Y", "documents": [{"type": "passport", "number": ""}]},
        {"name": "X Y", "documents": [{"type": "library_card", "number": "1"}]},
    ],
)
def test_invalid_subjects_are_422(api_env, payload):
    r = api_env["client"].post(
        "/screenings", json=payload, headers=api_env["auth"]["operator"]
    )
    assert r.status_code == 422


def test_unauthenticated_is_401(api_env):
    assert (
        api_env["client"].post("/screenings", json={"name": "A B"}).status_code == 401
    )


def test_intake_is_audited_without_pii(api_env):
    r = api_env["client"].post(
        "/screenings",
        json={"name": "Ingrid Solheimsen", "dob": "1984-03-02"},
        headers=api_env["auth"]["operator"],
    )
    db = api_env["factory"]()
    events = db.query(AuditEvent).filter_by(event_type="screening.submitted").all()
    assert len(events) == 1
    assert events[0].payload["run_id"] == r.json()["run_id"]
    blob = str(events[0].payload) + str(events[0].description)
    assert "Solheimsen" not in blob and "1984" not in blob
    db.close()


def test_optional_kyb_link_is_kept(api_env):
    from app.models.entity import Entity

    db = api_env["factory"]()
    entity = Entity(canonical_name="Northwind Traders Inc")
    db.add(entity)
    db.commit()
    entity_id = entity.id
    db.close()

    r = api_env["client"].post(
        "/screenings",
        json={"name": "Ingrid Solheimsen", "kyb_entity_id": entity_id},
        headers=api_env["auth"]["operator"],
    )
    db = api_env["factory"]()
    run = db.get(ScreeningRun, r.json()["run_id"])
    assert db.get(ScreeningSubject, run.subject_id).kyb_entity_id == entity_id
    db.close()
