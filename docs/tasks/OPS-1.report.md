# OPS-1 report

## 1. Files changed

- `VERSION` (new): `0.1.0`.
- `backend/app/core/version.py` (new): reads `VERSION` (lookup `/app/VERSION`, then repo root; one SemVer line, else `VersionError`), `GIT_SHA` (hex or `null`), alembic head from the script directory (not a DB query).
- `backend/app/api/routes/version.py` (new): `GET /version` with exactly `version`, `git_sha`, `migration_head`.
- `backend/app/api/router.py`: mounts it beside health, open.
- `backend/app/main.py`: `version=read_version()`; a bad file stops startup.
- `backend/tests/unit/test_version.py` (new): good/missing/malformed file, route body and no token, equality test.
- `backend/tests/unit/test_route_auth_guard.py`: `/api/v1/version` in the open allowlist with its reason.
- `.github/workflows/ci.yml`: one step comparing `VERSION`, pyproject and package.json.
- `frontend/src/app/api/health/route.ts`: adds `version` (`NEXT_PUBLIC_APP_VERSION`, else package.json).
- `frontend/src/lib/api.ts`: `shellApi.version` and the `ApiVersion` type.
- `frontend/src/components/auth/ApiVersionLine.tsx` (new, not in the allowed list): shared one-line component, used by both places so the failure wording lives once.
- `frontend/src/components/shell/Account.tsx`, `frontend/src/app/login/LoginForm.tsx`: render the line (existing `accountMeta` and `hint` classes, no CSS change).
- `backend/tests/integration/test_auth_api.py`: `/api/v1/version` added to that test's own open-path set (an allowlist; no assertion weakened).
- Tests: `ApiVersionLine.test.tsx`, `app/api/health/route.test.ts` (new); `Account.test.tsx`, `LoginForm.test.tsx` (new cases); `components/auth/AppFrame.test.tsx` (its `shellApi` mock gained `version`; without it two tests threw. No assertion changed).

## 2. What I did

All five build items. Failure shows "API version could not be read", never a number. Image note: `/app/VERSION` needs the release workflow to copy `VERSION` into the backend build context (`backend/Dockerfile` is outside this task); in a checkout the repo-root file is used.

Break-it checks: `VERSION` set to 0.1.1 made the equality test fail; `_SHA` regex loosened made two sha tests fail; hard-coded failure text and dropped `NEXT_PUBLIC_APP_VERSION` made 5 frontend tests fail. All restored.

## 3. Gates

- `ruff check app tests`: All checks passed!
- `mypy app`: Success: no issues found in 242 source files
- backend unit: 3458 passed, 10 skipped in 63.66s
- backend integration (`qradar_obs_ops1`), full run: 1 failed, 1285 passed, 3470 deselected in 1051.18s (0:17:31). The one failure was `test_auth_api.py::test_session_mode_refuses_every_route_without_a_cookie`, which keeps its own open-path set; I added `/api/v1/version` to it (see section 1). Re-run of that file: 18 passed in 30.15s. The full suite was not re-run after that one-line change.
- frontend `npm ci` ok; `npm run lint`: No ESLint warnings or errors; `tsc --noEmit` exit 0; `npx vitest run --no-cache`: Test Files 290 passed (290), Tests 3193 passed (3193); `npm run build` ok.

## 4. Screenshots (`/home/efe/Documents/QRadar-Dash-work/ops1/screenshots/`)

Fixture `fixture.mjs` (8121), production build on 3121, Chrome on 9121. "ok" = version served, "fail" = fixture answers 503. Account menu is on `/account`.

- `login-ok-1440x900.png`, `login-ok-1280x800.png`, `login-ok-1024x768.png`
- `login-fail-1440x900.png`, `login-fail-1280x800.png`, `login-fail-1024x768.png`
- `account-menu-ok-1440x900.png`, `account-menu-ok-1280x800.png`, `account-menu-ok-1024x768.png`
- `account-menu-fail-1440x900.png`, `account-menu-fail-1280x800.png`, `account-menu-fail-1024x768.png`

Seen: sign-in shows "API version 0.1.0" in small muted text under the button; the open menu shows it between "Change password" and "Sign out"; on failure both say "API version could not be read". No horizontal overflow at any viewport. At 1024x768 the `/account` page is 774px high (6px vertical scroll) with the menu closed too, so it is not caused by this change; I did not touch it.

## 5. Open points

- The 6px vertical scroll at 1024x768 on `/account` (pre-existing shell).
- `backend/Dockerfile`/release workflow must place `VERSION` at `/app/VERSION` and pass `GIT_SHA`; out of scope here.
- Inherited state: the tree was clean, nothing from the previous agent.
