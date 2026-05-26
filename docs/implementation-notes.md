# Implementation Notes

Running log of decisions, deviations, tradeoffs, and surprises during
implementation. Written for human review, tied to build-plan tickets.

---

## 2026-05-26 — P0-T1: Resolve blocking Open Decisions

**Decision (Open Decision #1 — backend stack): Python + FastAPI.** Confirmed by the
user. Rationale per ARCHITECTURE.md: the verification/enrichment/parsing domain
(WHOIS/DNS, address/phone normalization, sanctions, Playwright extraction) — which
*is* the product — has the strongest ecosystem in Python. Frontend stays React+TS.
Alternative (Node/TS) was rejected; it would unify language with the FE but weaken
the enrichment ecosystem.

**Decision (Open Decision #2 — job orchestration): Celery + Redis.** Follows from #1;
mature and simple fit for the async per-run pipeline model.

**Decision (Open Decision #3 — `shared/` contents): OpenAPI-generated TS types.** The
FastAPI backend emits an OpenAPI schema; the frontend consumes generated TS types
rather than hand-maintained duplicates. `shared/` holds the contract, not runtime code.

**Still open / deferred:** #4 HQ map provider, #5 data-source access/licensing,
#6 co-primary tiebreaker, #7 PII retention policy — to be resolved at the tickets
that need them (per BUILD_PLAN.md).

**Effect:** unblocks P0-T2 (monorepo scaffold). All build tickets now have a
confirmed stack.

---

## 2026-05-26 — P0-T2: Monorepo scaffold

**`vite.config.ts` added to frontend/.** The ticket spec listed `package.json`,
`tsconfig.json`, `index.html`, `src/main.tsx`, `src/App.tsx` as the FE files.
`vite.config.ts` is not listed but is required for `@vitejs/plugin-react` to load
(Vite errors without it when the plugin is declared). Treated as part of the
minimal runnable skeleton, not a scope expansion. Includes a `/api` proxy entry
pointing at the backend dev server (`localhost:8000`) to avoid CORS friction in
local dev.

**`@types/react` and `@types/react-dom` added to devDependencies.** Required for
TypeScript to compile `.tsx` files. Not a new runtime dependency.

**`hatchling` chosen as build backend for `pyproject.toml`.** Zero-config for a
flat `app/` layout; no `src/` wrapper needed. Consistent with the "simple over
clever" rule. Alternative (`setuptools`) would require a `setup.cfg` or explicit
`find:`. No difference in practice at scaffold time.

**Dependency install deferred.** Neither `pip install` nor `npm install` was run.
Deps are correctly declared; install/verify steps are documented in
`docs/implementation.md § Validation`. P0-T3 will establish the canonical install
path in CI.

---

## 2026-05-26 — P0-T3: Lint, test harness, and CI

**Backend deps pinned to exact versions to avoid pip backtracking.** The local
Python is 3.10.10 (pyproject.toml targets 3.11; CI images use 3.11-slim and will
be fine). Open-ended `>=` constraints caused the pip resolver to download 80+
ruff/pytest/httpcore wheels locally. Fixed by pinning to known-compatible exact
versions in `pyproject.toml`: `fastapi==0.111.0`, `starlette==0.37.2`,
`pydantic==2.7.4` (runtime), `pytest==8.3.5`, `httpx==0.27.2`, `ruff==0.4.10`
(dev). Tradeoff: slightly more maintenance overhead on upgrades; payoff: reliable
CI installs. In CI (Python 3.11-slim), the constraints are the same and will
resolve identically.

**`httpx` added as dev dep.** FastAPI's `TestClient` wraps `httpx`; omitting it
caused an `ImportError` at test collection time. Not a new runtime dep.

**`[tool.hatch.build.targets.wheel] packages = ["app"]` added to pyproject.toml.**
Hatchling couldn't auto-discover the package because the directory name `app/`
doesn't match the project name `entityiq-backend`. This is a build config fix,
not a scope expansion; it's required for `pip install .` to succeed in CI.

**Frontend ESLint uses the `.eslintrc.cjs` (legacy) format.** The project uses
`"type": "module"` in `package.json`, which means `.eslintrc.js` would fail in CJS
mode. `.eslintrc.cjs` forces CommonJS evaluation, which is the correct pattern for
eslint v8 with ESM projects.

**vitest config embedded in `vite.config.ts`** (via `/// <reference types="vitest" />`
triple-slash directive and a `test:` block). This avoids a separate config file and
is the recommended pattern for Vite + vitest setups.

**`@testing-library/react` without `@testing-library/jest-dom`.** The two test
assertions (`getDefined()`) use vitest's built-in `expect` with `.toBeDefined()`,
so `jest-dom` matchers are not needed. Kept scope minimal.

**Validation results (all passing locally):**
- `ruff check .` → "All checks passed!"
- `ruff format --check .` → "4 files already formatted"
- `pytest -v` → 2 passed in 0.77s
- `npm run lint` → exit 0, no warnings
- `npm run test` (vitest run) → 2 passed in 1.47s
