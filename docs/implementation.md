# Implementation

## Scope Implemented
- Requested scope: P0-T2 — Monorepo scaffold
- Related phase: Phase 0 — Decisions & Scaffolding
- Related ticket(s): P0-T2

## Approach
- High-level strategy: Create the minimal monorepo skeleton directories and files
  for the confirmed stack (Python + FastAPI backend, React + TypeScript frontend).
  No logic beyond `/health`, no linting config, no test runner, no database — those
  are P0-T3 and P0-T4.
- Key decisions:
  - `backend/pyproject.toml` uses `hatchling` as the build backend (standard,
    zero-config for a flat src layout). `ruff` and `pytest` are declared as
    `[project.optional-dependencies].dev`, not installed by default — consistent
    with "declare deps correctly" without requiring install at scaffold time.
  - `frontend/vite.config.ts` is included (Vite requires it for the `@vitejs/plugin-react`
    plugin); the proxy entry (`/api → http://localhost:8000`) avoids CORS friction
    in local dev without introducing a new dependency.
  - `frontend/tsconfig.json` uses `"moduleResolution": "bundler"` (required for
    `allowImportingTsExtensions` with Vite 5 / TS 5).
  - `shared/README.md` and `tests/README.md` are plain placeholders — no code, no
    config. Content describes what each directory will hold and which ticket sets
    it up.
- Assumptions:
  - No `.gitkeep` files existed in `shared/` or `tests/` (confirmed by inspection —
    neither directory existed before this ticket).
  - Python 3.11+ is the runtime target (per ARCHITECTURE.md confirmed stack).
  - React 18 is current stable — `react@^18.3.1` is appropriate.

---

## Implementation Plan
1. Create `backend/` directory and `backend/app/` subdirectory.
2. Write `backend/pyproject.toml` declaring runtime + dev deps.
3. Write `backend/app/__init__.py` (empty).
4. Write `backend/app/main.py` with FastAPI app + `GET /health`.
5. Create `frontend/src/` directory.
6. Write `frontend/package.json` (react, react-dom, typescript, vite, plugin-react, @types).
7. Write `frontend/tsconfig.json`.
8. Write `frontend/vite.config.ts` (plugin-react + dev proxy).
9. Write `frontend/index.html`.
10. Write `frontend/src/main.tsx`.
11. Write `frontend/src/App.tsx`.
12. Create `shared/` and write `shared/README.md`.
13. Create `tests/` and write `tests/README.md`.
14. Validate Python syntax for `main.py` and `__init__.py`.
15. Write `docs/implementation.md` (this file).
16. Append P0-T2 entry to `docs/implementation-notes.md`.
17. Update `docs/BUILD_PLAN.md` status.

---

## Code Changes

### File: backend/pyproject.toml
- Change summary: New file. Declares project metadata, Python ≥ 3.11, runtime deps
  (fastapi, uvicorn[standard]), and dev deps (pytest, ruff). Pytest testpaths set to
  `tests`. Ruff configured for E/F/I rules at line length 88.

### File: backend/app/__init__.py
- Change summary: New empty file. Makes `app` a Python package.

### File: backend/app/main.py
- Change summary: New file. Creates the FastAPI application instance and exposes a
  single `GET /health` endpoint returning `{"status": "ok", "service": "entityiq-backend", "version": "0.1.0"}`.

### File: frontend/package.json
- Change summary: New file. Declares `entityiq-frontend` v0.1.0 with runtime deps
  react + react-dom and dev deps typescript, vite, @vitejs/plugin-react, and @types.
  Scripts: dev / build / preview.

### File: frontend/tsconfig.json
- Change summary: New file. Standard Vite + React + TS 5 config with strict mode,
  `moduleResolution: bundler`, `jsx: react-jsx`, `noEmit: true`.

### File: frontend/vite.config.ts
- Change summary: New file. Configures `@vitejs/plugin-react`, dev server port 5173,
  and a proxy rule `/api → http://localhost:8000` for local development.

### File: frontend/index.html
- Change summary: New file. Minimal HTML entry point mounting `#root` and loading
  `/src/main.tsx` as an ES module.

### File: frontend/src/main.tsx
- Change summary: New file. React 18 entry point using `createRoot`, renders `<App />`
  inside `<StrictMode>`. Throws clearly if `#root` is absent.

### File: frontend/src/App.tsx
- Change summary: New file. Minimal placeholder component rendering the app name
  and one-line description. No state, no imports beyond the implicit JSX transform.

### File: shared/README.md
- Change summary: New file. Placeholder documenting that this directory will hold
  the FastAPI-generated `openapi.json` and the TS types derived from it, configured
  in P0-T3.

### File: tests/README.md
- Change summary: New file. Placeholder documenting that cross-cutting / E2E tests
  live here; test harness config is P0-T3.

---

## Acceptance Criteria Mapping

- Criterion: PRD § Suggested Architecture § Monorepo — `frontend/ backend/ shared/ docs/ tests/` present.
  - Implementation: All five directories exist. `docs/` was pre-existing.
  - File(s): `frontend/`, `backend/`, `shared/`, `tests/`

- Criterion: PRD § Code Quality Expectations (monorepo) — skeleton in place.
  - Implementation: Each directory has at least a minimal file; no spurious files added.
  - File(s): all scaffold files

- Criterion: ARCHITECTURE § 1 — FastAPI backend, React/TS frontend.
  - Implementation: `backend/app/main.py` uses FastAPI; `frontend/` is a Vite + React + TS project.
  - File(s): `backend/app/main.py`, `frontend/package.json`, `frontend/tsconfig.json`

- Criterion: `GET /health` returns a status payload.
  - Implementation: Route defined in `main.py` returning `{"status": "ok", "service": ..., "version": ...}`.
  - File(s): `backend/app/main.py`

---

## Build Plan Mapping

- Ticket: P0-T2 — Monorepo scaffold
  - Status: Complete
  - What was completed: All directories and minimal app skeletons created; Python syntax validated.
  - Remaining work: None for this ticket. Lint/test/CI config (P0-T3) and Postgres (P0-T4) are separate.

---

## Validation

- Python syntax check: `python3 -c "import ast; ast.parse(open('backend/app/main.py').read())"` — passed.
- Python syntax check: `python3 -c "import ast; ast.parse(open('backend/app/__init__.py').read())"` — passed.
- File structure verified with `find` — all required files present.
- Dependency install not performed (no venv, no `npm install`); deps are declared correctly and will be resolved by P0-T3.

Manual verification steps (no further blocker — can be done by any developer):
1. `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && uvicorn app.main:app --reload`
   — confirm `curl http://localhost:8000/health` returns `{"status":"ok","service":"entityiq-backend","version":"0.1.0"}`.
2. `cd frontend && npm install && npm run build`
   — confirm TypeScript compiles cleanly and Vite outputs `dist/`.
3. `cd frontend && npm run dev`
   — confirm the dev server starts at `http://localhost:5173` and renders the placeholder page.

---

## Open Issues
- No blockers for P0-T2.
- Vite's `vite.config.ts` is a minor scope addition versus the ticket spec (which listed only `package.json`, `tsconfig.json`, `index.html`, `src/main.tsx`, `src/App.tsx`). It is required for the build to work with `@vitejs/plugin-react` and is considered part of the minimal runnable skeleton. Noted in `implementation-notes.md`.
- TypeScript types for react (`@types/react`, `@types/react-dom`) included in `devDependencies` — required for `tsx` compilation; not a scope expansion.

---

## BUILD_PLAN Update

See `docs/BUILD_PLAN.md`:
- P0-T2 status: **Complete**
- Current ticket: **P0-T3 — Lint, test harness, and CI**
- Blockers: None
