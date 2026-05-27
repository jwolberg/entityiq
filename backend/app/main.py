from fastapi import FastAPI

from app.api.reports import router as reports_router
from app.api.reviews import router as reviews_router
from app.api.submissions import router as submissions_router
from app.auth.operator import auth_router

app = FastAPI(
    title="EntityIQ API",
    description="Enterprise Business Verification & Risk Intelligence Platform",
    version="0.1.0",
)

app.include_router(submissions_router)
app.include_router(reports_router)
app.include_router(auth_router)
app.include_router(reviews_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "entityiq-backend", "version": "0.1.0"}
