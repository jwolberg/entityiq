# Implementation

## Scope Implemented
- Requested scope: P0-T3 — Lint, test harness, and CI
- Related phase: Phase 0 — Decisions & Scaffolding
- Related ticket(s): P0-T3

## Approach
- Configure `ruff` (lint + format) and `pytest` for the backend with a real passing
  test against `GET /health` using FastAPI's `TestClient`.
- Configure ESLint (TypeScript + React) and vitest for the frontend with a real
  passing test that renders `<App/>` and asserts on content.
- Add `.gitlab-ci.yml` with `lint` and `test` stages covering both BE and FE.
- Pin all dependency versions to avoid pip resolver backtracking (see
  implementation-notes.md for rationale).
- Fix hatchling package-discovery for the `app/` directory (required for
  `pip install .` in CI).

**Key decisions:**
- Pinned exact versions (`fastapi==0.111.0`, `starlette==0.37.2`, `pydantic==2.7.4`,
  `pytest==8.3.5`, `httpx==0.27.2`, `ruff==0.4.10`) rather than open ranges to
  ensure CI installs are fast and deterministic.
- Embedded vitest config in `vite.config.ts` via triple-slash reference (avoids a
  separate config file; standard vitest+vite pattern).
- ESLint config in `.eslintrc.cjs` (not `.js`) because `package.json` sets
  `"type": "module"` — CJS extension forces correct evaluation for ESLint v8.

**Assumptions:**
- GitLab CI runner has network access to PyPI and npmjs.com.
- `python:3.11-slim` and `node:20-slim` images are available on the self-hosted
  GitLab at labs.gauntletai.com.

---

## Implementation Plan
1. Create `backend/tests/__init__.py` and `backend/tests/test_health.py`.
2. Update `backend/pyproject.toml`: add `httpx` dev dep, `ruff.format` config,
   `hatch.build.targets.wheel` packages, pin versions.
3. Update `frontend/package.json`: add devDeps (eslint, @typescript-eslint/*,
   vitest, @testing-library/react, jsdom, eslint plugins) and lint/test scripts.
4. Create `frontend/.eslintrc.cjs`.
5. Update `frontend/vite.config.ts`: add vitest config block.
6. Create `frontend/src/App.test.tsx`.
7. Create `.gitlab-ci.yml`.
8. Run backend validation: `ruff check`, `ruff format --check`, `pytest`.
9. Run frontend validation: `npm install`, `npm run lint`, `npm run test`.

---

## Code Changes

### File: `backend/tests/__init__.py`
- Change summary: New empty file; makes `tests/` a proper Python package.
- Contents: (empty)

### File: `backend/tests/test_health.py`
- Change summary: New file; two pytest tests via FastAPI TestClient.
```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200

def test_health_returns_expected_body():
    response = client.get("/health")
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "entityiq-backend"
    assert body["version"] == "0.1.0"
```

### File: `backend/pyproject.toml`
- Change summary: Pin deps; add httpx dev dep; add ruff.format config; add
  hatch.build.targets.wheel packages declaration.
- Key diffs:
  - `fastapi==0.111.0`, `starlette==0.37.2`, `pydantic==2.7.4` pinned in runtime deps
  - `httpx==0.27.2` added to dev deps
  - `[tool.ruff.format]` section added
  - `[tool.hatch.build.targets.wheel] packages = ["app"]` added

### File: `frontend/package.json`
- Change summary: Add eslint, @typescript-eslint/*, vitest, @testing-library/react,
  jsdom, eslint-plugin-react-hooks, eslint-plugin-react-refresh as devDeps; add
  `lint` and `test` scripts.

### File: `frontend/.eslintrc.cjs`
- Change summary: New file; ESLint v8 config for TypeScript + React.
```js
module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  ignorePatterns: ["dist", ".eslintrc.cjs"],
  parser: "@typescript-eslint/parser",
  plugins: ["react-refresh"],
  rules: {
    "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
  },
};
```

### File: `frontend/vite.config.ts`
- Change summary: Added `/// <reference types="vitest" />` and `test:` block for
  jsdom environment and globals.

### File: `frontend/src/App.test.tsx`
- Change summary: New file; two vitest tests rendering `<App/>` via
  `@testing-library/react` and asserting on heading and description text.

### File: `.gitlab-ci.yml`
- Change summary: New file; two stages (`lint`, `test`), four jobs:
  `backend-lint`, `backend-test` (python:3.11-slim), `frontend-lint`,
  `frontend-test` (node:20-slim).

---

## Acceptance Criteria Mapping

- Criterion: PRD § Technical Success — test coverage
  - Implementation: pytest test suite for backend health endpoint; vitest test
    suite for frontend App component. Both run in CI.
  - Files: `backend/tests/test_health.py`, `frontend/src/App.test.tsx`,
    `.gitlab-ci.yml`

- Criterion: CLAUDE.md § Validation — run lint and tests before considering work
  complete
  - Implementation: `ruff check` + `ruff format --check` (backend); `eslint`
    (frontend); both in CI. Tests run in `test` stage.
  - Files: `backend/pyproject.toml`, `frontend/.eslintrc.cjs`, `.gitlab-ci.yml`

- Criterion: P0-T3 objective — lint/format, test runners, CI with lint + test
  stages
  - Implementation: All four tools configured (ruff, pytest, eslint, vitest);
    `.gitlab-ci.yml` with `lint` and `test` stages covering both workspaces.
  - Files: all files listed above

---

## Build Plan Mapping

- Ticket: P0-T3 — Lint, test harness, and CI
  - Status: Complete
  - What was completed: ruff (lint + format) configured and passing; pytest with
    one health test passing (2 assertions); ESLint configured and passing;
    vitest with one App render test passing (2 assertions); `.gitlab-ci.yml`
    with 4 jobs across 2 stages created.
  - Remaining work: none

---

## Validation

**Backend — ran locally:**
```
cd backend
python3 -m venv .venv
.venv/bin/pip install (pinned deps)

.venv/bin/ruff check .
  -> All checks passed!

.venv/bin/ruff format --check .
  -> 4 files already formatted

.venv/bin/pytest -v
  -> tests/test_health.py::test_health_returns_200 PASSED
  -> tests/test_health.py::test_health_returns_expected_body PASSED
  -> 2 passed in 0.77s
```

**Frontend — ran locally:**
```
cd frontend
npm install
  -> exit 0 (with audit warnings unrelated to lint/test tools)

npm run lint
  -> exit 0, no output (no warnings, no errors)

npm run test
  -> src/App.test.tsx  (2 tests) 88ms
  -> Test Files: 1 passed (1)
  -> Tests: 2 passed (2)
```

**Manual verification steps (for CI):**
- Push to a branch on labs.gauntletai.com; confirm the 4 jobs appear in the
  pipeline and pass.
- Confirm `backend-lint`, `backend-test`, `frontend-lint`, `frontend-test` all
  show green.

---

## Open Issues

- The local Python is 3.10.10; `pyproject.toml` specifies `requires-python = ">=3.11"`.
  CI uses `python:3.11-slim` which satisfies the constraint. Local dev on Python 3.10
  works in practice (no 3.11-only syntax used in the scaffold). This is a minor
  discrepancy to clean up when developers set up local environments with pyenv/asdf.
- `npm audit` reports vulnerability warnings (unrelated to test/lint tooling). These
  are in transitive deps of `vite`/`eslint` and are not security-blocking for a
  dev-only toolchain; address them when upgrading the FE dependency tree.
- CI installs all deps on every job (no caching configured). GitLab CI caching can
  be added in a follow-on pass (P3-T5 or infra work) once the CI topology is stable.

---

## BUILD_PLAN Update
- Current phase: Phase 0 — Decisions & Scaffolding
- Current ticket: P0-T4 — Postgres + migrations + core data model
- P0-T3 status: Complete
- Blockers: None
- Recommended next ticket: P0-T4
