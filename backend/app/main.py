from fastapi import FastAPI

app = FastAPI(
    title="EntityIQ API",
    description="Enterprise Business Verification & Risk Intelligence Platform",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "entityiq-backend", "version": "0.1.0"}
