import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from app.api.api_clients import router as api_clients_router
from app.api.audit import router as audit_router
from app.api.ownership import router as ownership_router
from app.api.reanalysis import router as reanalysis_router
from app.api.reports import router as reports_router
from app.api.reviews import router as reviews_router
from app.api.submissions import router as submissions_router
from app.api.workflow import router as workflow_router
from app.auth.examiner_guard import forbid_examiner_writes
from app.auth.operator import auth_router, configure_session_store
from app.screening.api import router as screening_router
from app.ui import mount_ui


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Try to back operator sessions with Redis (ticket 0024). Falls back to
    # in-memory sessions with a logged warning if Redis isn't reachable, so
    # dev/demo keep working without it.
    configure_session_store()
    yield


app = FastAPI(
    # Examiners are read-only on every route (ticket 0042).
    dependencies=[Depends(forbid_examiner_writes)],
    title="EntityIQ API",
    description="Enterprise Business Verification & Risk Intelligence Platform",
    version="0.1.0",
    lifespan=_lifespan,
)

app.include_router(submissions_router)
app.include_router(reports_router)
app.include_router(auth_router)
app.include_router(reviews_router)
app.include_router(reanalysis_router)
app.include_router(workflow_router)
app.include_router(audit_router)
app.include_router(api_clients_router)
app.include_router(ownership_router)
app.include_router(screening_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "entityiq-backend", "version": "0.1.0"}


# Hosted deploys serve the built UI from this process (ticket 0054). Mounted
# last so every API route above takes precedence over static files.
if _ui_dist := os.environ.get("ENTITYIQ_UI_DIST"):
    mount_ui(app, Path(_ui_dist))
