"""Read-only examiner role (IS2-T7, ticket 0042; PRD-IDV C6)."""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from app.main import app
from app.models.audit_event import AuditEvent
from tests.screening.conftest import load_people

WRITE = {"POST", "PUT", "PATCH", "DELETE"}
# Writes an examiner may make: ending their session, verifying a decision.
ALLOWED = {
    ("POST", "/auth/sign-out"),
    ("POST", "/auth/sign-in"),
    ("POST", "/screenings/{run_id}/replay"),
}


def _write_routes():
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods & WRITE:
            if (method, route.path) not in ALLOWED:
                yield method, route.path


def test_there_are_write_routes_to_check():
    assert len(list(_write_routes())) >= 5


@pytest.mark.parametrize(("method", "path"), sorted(_write_routes()))
def test_examiner_gets_403_on_every_write_route(api_env, method, path):
    url = re.sub(r"\{[^}]+\}", "does-not-exist", path)
    r = api_env["client"].request(
        method, url, json={}, headers=api_env["auth"]["examiner"]
    )
    assert r.status_code == 403, (method, path, r.status_code, r.text)


def test_examiner_write_attempts_are_audited(api_env):
    api_env["client"].post(
        "/screenings", json={"name": "A B"}, headers=api_env["auth"]["examiner"]
    )
    db = api_env["factory"]()
    ev = db.query(AuditEvent).filter_by(event_type="operator.access_denied").all()
    assert any(e.payload.get("reason") == "examiner_read_only" for e in ev)
    db.close()


def test_examiner_can_read_screenings_audit_and_replay(api_env):
    load_people(api_env["factory"], [{"name": "Teodor Vasilescu"}])
    run_id = (
        api_env["client"]
        .post(
            "/screenings",
            json={"name": "Teodor Vasilescu"},
            headers=api_env["auth"]["operator"],
        )
        .json()["run_id"]
    )
    h = api_env["auth"]["examiner"]
    c = api_env["client"]

    assert c.get("/screenings", headers=h).status_code == 200
    assert c.get(f"/screenings/{run_id}", headers=h).status_code == 200
    assert c.get("/audit/events", headers=h).status_code == 200
    assert c.post(f"/screenings/{run_id}/replay", headers=h).status_code == 200


def test_operators_still_cannot_read_the_global_audit_log(api_env):
    assert (
        api_env["client"]
        .get("/audit/events", headers=api_env["auth"]["operator"])
        .status_code
        == 403
    )


def test_seed_creates_an_examiner_demo_account():
    from app.seed import DEMO_ACCOUNTS

    assert ("examiner@demo.entityiq.dev", "Demo Examiner", "examiner") in DEMO_ACCOUNTS
