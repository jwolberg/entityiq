"""P4-T6 — server-side sign-out and session expiry."""

import pytest
from fastapi.testclient import TestClient

from app.auth import operator as op_auth
from app.main import app


@pytest.fixture(autouse=True)
def _clean_sessions():
    yield
    op_auth._clear_all_sessions()


def test_sign_out_invalidates_token_server_side():
    token = op_auth._new_session_token("op-1")
    client = TestClient(app)
    resp = client.post("/auth/sign-out", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204
    assert op_auth._get_operator_id_from_token(token) is None


def test_sign_out_without_token_is_harmless():
    assert TestClient(app).post("/auth/sign-out").status_code == 204


def test_expired_session_is_rejected(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(op_auth, "_now", lambda: now[0])
    token = op_auth._new_session_token("op-1")
    assert op_auth._get_operator_id_from_token(token) == "op-1"

    now[0] += op_auth.SESSION_TTL_SECONDS + 1
    assert op_auth._get_operator_id_from_token(token) is None
