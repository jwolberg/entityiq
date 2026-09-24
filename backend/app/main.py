from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.audit import router as audit_router
from app.api.reanalysis import router as reanalysis_router
from app.api.reports import router as reports_router
from app.api.reviews import router as reviews_router
from app.api.submissions import router as submissions_router
from app.api.workflow import router as workflow_router
from app.auth.operator import auth_router, configure_session_store


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Try to back operator sessions with Redis (ticket 0024). Falls back to
    # in-memory sessions with a logged warning if Redis isn't reachable, so
    # dev/demo keep working without it.
    configure_session_store()
    yield


app = FastAPI(
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "entityiq-backend", "version": "0.1.0"}
