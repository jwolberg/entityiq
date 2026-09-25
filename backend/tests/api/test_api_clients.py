"""Tests for lead-only API-key provisioning (ticket 0026).

  POST   /api-clients            — create (full key shown once, lead only)
  GET    /api-clients            — list (prefix + metadata only, lead only)
  POST   /api-clients/{id}/revoke — revoke (lead only)

Covers: role gating (operator → 403), auditing on create/revoke, the hash
never being returned/logged, and a revoked key being rejected on its next
authenticated request.
"""

from __future__ import annotations

import unittest.mock as mock
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.api_clients import _get_db as api_clients_get_db
from app.api.submissions import _get_db as submissions_get_db
from app.auth.operator import _clear_all_sessions, _new_session_token
from app.auth.operator import _get_db as operator_get_db
from app.auth.service import _get_db as service_get_db
from app.db.session import Base
from app.main import app
from app.models.api_client import ApiClient
from app.models.audit_event import AuditEvent
from app.models.operator import Operator

_VALID_PAYLOAD = {
    "company_name": "Acme Corp",
    "work_email": "cto@acme.example",
    "company_domain": "acme.example",
    "country": "US",
}


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def Maker(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture
def client(Maker):
    def override():
        db = Maker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[api_clients_get_db] = override
    app.dependency_overrides[operator_get_db] = override
    app.dependency_overrides[service_get_db] = override
    app.dependency_overrides[submissions_get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
    _clear_all_sessions()


def _operator(Maker, role: str) -> tuple[str, str]:
    db = Maker()
    try:
        op = Operator(
            email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
            full_name=f"Test {role.title()}",
            role=role,
        )
        db.add(op)
        db.commit()
        return op.id, _new_session_token(op.id)
    finally:
        db.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth / role gating
# ---------------------------------------------------------------------------


def test_create_requires_auth(client):
    resp = client.post("/api-clients", json={"name": "acme"})
    assert resp.status_code == 401


def test_list_requires_auth(client):
    assert client.get("/api-clients").status_code == 401


def test_operator_gets_403_on_create(client, Maker):
    _, token = _operator(Maker, "operator")
    resp = client.post("/api-clients", json={"name": "acme"}, headers=_auth(token))
    assert resp.status_code == 403


def test_operator_gets_403_on_list(client, Maker):
    _, token = _operator(Maker, "operator")
    resp = client.get("/api-clients", headers=_auth(token))
    assert resp.status_code == 403


def test_operator_gets_403_on_revoke(client, Maker):
    _, token = _operator(Maker, "operator")
    resp = client.post("/api-clients/does-not-exist/revoke", headers=_auth(token))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_lead_creates_key_and_sees_full_key_once(client, Maker):
    _, token = _operator(Maker, "lead")
    resp = client.post(
        "/api-clients", json={"name": "acme-integration"}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "acme-integration"
    assert body["api_key"].startswith("eiq_")
    assert body["key_prefix"] == body["api_key"][:12]
    assert body["active"] is True
    assert "key_hash" not in body


def test_create_is_audited(client, Maker):
    lead_id, token = _operator(Maker, "lead")
    resp = client.post(
        "/api-clients", json={"name": "audited-co"}, headers=_auth(token)
    )
    client_id = resp.json()["id"]

    db = Maker()
    try:
        event = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "operator.api_client_created")
            .one()
        )
        assert event.operator_id == lead_id
        assert event.payload["api_client_id"] == client_id
    finally:
        db.close()


def test_duplicate_name_is_rejected(client, Maker):
    _, token = _operator(Maker, "lead")
    client.post("/api-clients", json={"name": "dup-co"}, headers=_auth(token))
    resp = client.post("/api-clients", json={"name": "dup-co"}, headers=_auth(token))
    assert resp.status_code == 409


def test_empty_name_is_rejected(client, Maker):
    _, token = _operator(Maker, "lead")
    resp = client.post("/api-clients", json={"name": "   "}, headers=_auth(token))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_lead_lists_prefix_and_metadata_only(client, Maker):
    _, token = _operator(Maker, "lead")
    client.post("/api-clients", json={"name": "list-me"}, headers=_auth(token))

    resp = client.get("/api-clients", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["name"] == "list-me"
    assert "key_prefix" in item
    assert "api_key" not in item
    assert "key_hash" not in item


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def test_lead_revokes_key(client, Maker):
    _, token = _operator(Maker, "lead")
    created = client.post(
        "/api-clients", json={"name": "revoke-me"}, headers=_auth(token)
    ).json()

    resp = client.post(f"/api-clients/{created['id']}/revoke", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is False

    db = Maker()
    try:
        row = db.get(ApiClient, created["id"])
        assert row.active is False
    finally:
        db.close()


def test_revoke_unknown_id_returns_404(client, Maker):
    _, token = _operator(Maker, "lead")
    resp = client.post("/api-clients/does-not-exist/revoke", headers=_auth(token))
    assert resp.status_code == 404


def test_revoke_is_audited(client, Maker):
    lead_id, token = _operator(Maker, "lead")
    created = client.post(
        "/api-clients", json={"name": "audit-revoke"}, headers=_auth(token)
    ).json()

    client.post(f"/api-clients/{created['id']}/revoke", headers=_auth(token))

    db = Maker()
    try:
        event = (
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "operator.api_client_revoked")
            .one()
        )
        assert event.operator_id == lead_id
        assert event.payload["api_client_id"] == created["id"]
    finally:
        db.close()


def test_revoked_key_is_rejected_on_next_request(client, Maker):
    _, token = _operator(Maker, "lead")
    created = client.post(
        "/api-clients", json={"name": "will-be-revoked"}, headers=_auth(token)
    ).json()
    full_key = created["api_key"]

    with mock.patch("app.pipeline.orchestrator.enqueue_run") as _mock_enqueue:
        _mock_enqueue.return_value = None

        # Works while active.
        ok = client.post(
            "/submissions", json=_VALID_PAYLOAD, headers={"X-API-Key": full_key}
        )
        assert ok.status_code == 202, ok.text

        client.post(f"/api-clients/{created['id']}/revoke", headers=_auth(token))

        rejected = client.post(
            "/submissions",
            json={**_VALID_PAYLOAD, "idempotency_key": "after-revoke"},
            headers={"X-API-Key": full_key},
        )
    assert rejected.status_code == 401


def test_duplicate_name_race_is_409_not_500(client, Maker, monkeypatch):
    """Review finding: SELECT-then-INSERT raced. Another request inserts the
    same name after our pre-check; the DB unique constraint must map to 409."""
    import app.api.api_clients as mod
    from app.auth.service import create_api_client as real_create

    def racing_create(name, db, **kw):
        other = Maker()
        real_create(name, other)  # the concurrent request wins
        other.close()
        return real_create(name, db, **kw)

    monkeypatch.setattr(mod, "create_api_client", racing_create)
    _, token = _operator(Maker, "lead")
    r = client.post("/api-clients", json={"name": "race"}, headers=_auth(token))
    assert r.status_code == 409, r.text
