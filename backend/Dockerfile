# EntityIQ backend image — serves both the API (uvicorn) and the Celery
# worker (docker-compose.yml overrides `command:` for the worker service).
#
# Runtime dependencies only (no `[dev]` extra) — lint/tests run outside the
# container via the shared venv (see docs/RUNBOOK.md).
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY app ./app

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
