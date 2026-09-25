"""Serving the built UI from the API process (ticket 0054).

On Cloud Run one service serves the SPA at ``/`` and the API under
``/api/*``, the same paths the Vite dev proxy gives the frontend locally.
It is opt-in via ENTITYIQ_UI_DIST so local dev and tests are unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ui import mount_ui


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>EntityIQ UI</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log('ui')")
    return tmp_path


@pytest.fixture
def client(dist: Path) -> TestClient:
    app = FastAPI()

    @app.get("/reports")
    def reports(q: str | None = None) -> dict:
        return {"route": "reports", "q": q}

    @app.post("/auth/sign-in")
    def sign_in(body: dict) -> dict:
        return {"echo": body}

    mount_ui(app, dist)
    return TestClient(app)


def test_root_serves_the_spa_and_its_assets(client):
    r = client.get("/")
    assert r.status_code == 200 and "EntityIQ UI" in r.text
    assert "text/html" in r.headers["content-type"]
    assert client.get("/assets/app.js").text == "console.log('ui')"


def test_api_prefix_is_stripped_for_every_method_and_keeps_the_query(client):
    assert client.get("/api/reports?q=acme").json() == {"route": "reports", "q": "acme"}
    r = client.post("/api/auth/sign-in", json={"email": "x"})
    assert r.json() == {"echo": {"email": "x"}}


def test_api_routes_win_over_static_files_and_unknown_paths_404(client):
    assert client.get("/reports").json()["route"] == "reports"
    assert client.get("/api/nope").status_code == 404
    assert client.get("/nope.txt").status_code == 404


def test_a_prefix_lookalike_is_not_stripped(client):
    """Only the exact /api segment is an API prefix, not /apifoo."""
    assert client.get("/apireports").status_code == 404


def test_missing_index_fails_fast(tmp_path):
    with pytest.raises(RuntimeError, match="index.html"):
        mount_ui(FastAPI(), tmp_path)


def test_the_real_app_does_not_serve_a_ui_without_the_env(monkeypatch):
    monkeypatch.delenv("ENTITYIQ_UI_DIST", raising=False)
    from app.main import app

    c = TestClient(app)
    assert c.get("/").status_code == 404
    assert c.get("/health").json()["status"] == "ok"
