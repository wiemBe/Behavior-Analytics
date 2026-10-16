# Task OPS-1 — one version: `VERSION`, the CI equality check, `GET /api/v1/version`, the UI line

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/OPERATIONS-DESIGN.md` §1 (binding) and §10.2's OPS-1 line. The user decided (D-20) that the
first version is **0.1.0**.

## Build

1. **`VERSION`** at the repository root, one line: `0.1.0`.
2. **The backend reads it.**
   - `backend/app/main.py` stops hard-coding `version="0.1.0"`.
   - A small module reads `VERSION`, from `/app/VERSION` in the image or the repository root in
     a checkout. Decide the lookup order and say it in the module docstring.
   - A missing or malformed file is a startup error with a clear message, never a silent
     fallback.
3. **`GET /api/v1/version`**: unauthenticated, mounted beside health
   (`backend/app/api/router.py`).
   - The body is `{"version", "git_sha", "migration_head"}`.
   - `git_sha` comes from an environment variable or build arg (`GIT_SHA`). When it is absent
     the value is `null`, never a made-up string.
   - `migration_head` is the head of `backend/alembic/versions/` as shipped, read from the
     Alembic script directory. It is not a live database query.
   - The route has no other field, and it is in the route-guard test's open allowlist with its
     reason.
4. **The CI equality check.** In `.github/workflows/ci.yml`, a step fails when `VERSION`,
   `backend/pyproject.toml`'s `version` and `frontend/package.json`'s `version` differ. Add a
   unit test that does the same comparison, so the rule also holds locally.
5. **The frontend.**
   - `frontend/src/app/api/health/route.ts` adds `version`, the frontend's build-time version.
     Use `NEXT_PUBLIC_APP_VERSION` if it is set at build, otherwise `package.json`'s version.
   - The account menu (`components/shell/Account.tsx`) and the sign-in page each show one line
     with the API version from `GET /api/v1/version`, in the existing type tokens.
   - When the request fails, the line says the version could not be read. It never shows a
     hard-coded `0.1.0`.
   - No new page and no banner.

## Tests

- Backend:
  - the version module (good file, missing file, malformed line);
  - the route's body and that it needs no token;
  - the equality test.
- Frontend:
  - the version line on success and on failure, in the account menu and on sign-in;
  - the health route's `version`.
- Break each new test once, then restore the code.

## Gates

- Backend, from `backend/` with `PYTHONPATH=.` and the main checkout's venv:
  - `ruff check app tests`, clean;
  - `mypy app`, 0 errors;
  - unit tests;
  - integration only on the `TEST_DATABASE_URL` your prompt gives.
- Frontend, from `frontend/` with `~/.local/node/bin` on `PATH`: `npm ci`, lint, typecheck,
  `npx vitest run --no-cache`, build.
- Screenshots: the account menu open, and the sign-in page, at 1440×900, 1280×800 and
  1024×768. Take them against a fixture under `/home/efe/Documents/QRadar-Dash-work/ops1/`, on
  ports 3121/8121/9121. Open them and say what you saw.

## Allowed files

- `VERSION` (new)
- `backend/app/main.py`, a new `backend/app/core/version.py`, `backend/app/api/router.py`, a new
  `backend/app/api/routes/version.py`
- `backend/tests/` (new tests, and the route-guard allowlist)
- `.github/workflows/ci.yml` (the one step)
- `frontend/src/app/api/health/route.ts`, `frontend/src/components/shell/Account.tsx`, the
  sign-in page component, `frontend/src/lib/api.ts` (the version call only), their tests
- `docs/tasks/OPS-1.report.md` (new)

Do not commit. Never write under `/tmp`. Stop every process you start by PID.
