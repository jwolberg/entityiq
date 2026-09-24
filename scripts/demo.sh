#!/usr/bin/env bash
# One-command local demo: API + operator UI on SQLite, pipeline run inline
# (no Postgres, Redis, or Docker needed). Idempotent — re-run any time.
#
#   ./scripts/demo.sh            # set up (first run) and start both servers
#   DEMO_DB=/tmp/x.db ./scripts/demo.sh
#
# Needs: python3.11+ and node 20+. Ctrl-C stops both servers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-5173}"
export DATABASE_URL="sqlite:///${DEMO_DB:-$ROOT/backend/entityiq-demo.db}"
export CELERY_TASK_ALWAYS_EAGER=true

PY="${PYTHON:-$(command -v python3.12 || command -v python3.11 || command -v python3)}"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || { echo "Python 3.11+ required (found $("$PY" --version))" >&2; exit 1; }

cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  echo "==> Creating backend virtualenv"
  "$PY" -m venv .venv
fi
if ! .venv/bin/python -c 'import app, fastapi, whois' 2>/dev/null; then
  echo "==> Installing backend"
  .venv/bin/pip install -q -e ".[dev]"
fi
echo "==> Migrating database ($DATABASE_URL)"
migrate_out=$(.venv/bin/alembic upgrade head 2>&1) || { echo "$migrate_out" >&2; exit 1; }
echo "==> Seeding demo data"
.venv/bin/python -m app.seed

cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  echo "==> Installing frontend"
  npm ci --silent
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

cd "$ROOT/backend"
.venv/bin/uvicorn app.main:app --port "$API_PORT" --log-level warning &
cd "$ROOT/frontend"
VITE_API_TARGET="http://localhost:$API_PORT" npx vite --port "$UI_PORT" --strictPort --logLevel warn &

echo
echo "EntityIQ demo running:"
echo "  UI   http://localhost:$UI_PORT"
echo "  API  http://localhost:$API_PORT/docs"
echo "Ctrl-C to stop."
wait
