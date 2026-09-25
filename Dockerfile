# EntityIQ hosted image (Cloud Run, ticket 0054): the operator UI and the API
# in one container. The UI is built with Vite and served by the API process at
# `/`, and the API answers under `/api/*` (app/ui.py). The same image runs the
# migrate and seed Cloud Run Jobs with a different command.
#
# Local dev keeps using backend/Dockerfile and frontend/Dockerfile via
# docker-compose. Build: see docs/runbooks/deploy-cloud-run.md.

FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/pyproject.toml backend/alembic.ini ./
COPY backend/app ./app
RUN pip install --no-cache-dir .
COPY --from=ui /ui/dist /app/ui

ENV ENTITYIQ_UI_DIST=/app/ui \
    PYTHONUNBUFFERED=1

# Cloud Run sets PORT (8080 by default).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
