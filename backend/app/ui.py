"""Serve the built operator UI from the API process (ticket 0054).

Hosted deploys run one container: the SPA at ``/`` and the API under
``/api/*``, the same paths the Vite dev proxy gives the frontend locally
(it strips ``/api`` before forwarding). Enabled only when
``ENTITYIQ_UI_DIST`` points at a ``vite build`` output directory.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

API_PREFIX = "/api"


class StripApiPrefix:
    """Rewrite ``/api/x`` to ``/x`` so API routes answer under the prefix."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path: str = scope["path"]
            if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
                scope = dict(scope)
                scope["path"] = path[len(API_PREFIX) :] or "/"
                raw = scope.get("raw_path")
                if raw:
                    scope["raw_path"] = raw[len(API_PREFIX) :] or b"/"
        await self.app(scope, receive, send)


def mount_ui(app: FastAPI, dist: Path) -> None:
    """Serve ``dist`` at ``/`` behind the API routes, and strip ``/api``.

    Call after every router is included: the static mount matches last, so
    API routes always win.
    """
    if not (dist / "index.html").is_file():
        raise RuntimeError(f"ENTITYIQ_UI_DIST has no index.html: {dist}")
    app.add_middleware(StripApiPrefix)
    app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
