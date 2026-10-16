# Task AUTH-2 — the login page, the session in the shell, user and role administration, LDAP settings (UI)

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a above all**
(screenshots are part of acceptance). Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative),
`frontend/src/app/tokens.css`, `docs/tasks/AUTH-1.md` and **`docs/tasks/AUTH-1.report.md`** (the
API this builds on: the routes in `backend/app/api/routes/auth.py`, `users.py`, `me.py`, their
schemas, the error shapes, the `must_change_password` gate, the CSRF origin rule, the lockout
message). PLANNER D-19 is the user's decision: local accounts **and** AD password over LDAP; no
group is read from AD; roles are assigned here by admins; a first-time LDAP user has no role.

## Build

1. **`/login`** (outside the shell's navigation): username, password, one submit. On 401 it shows
   the backend's message as sent ("Invalid username or password"), never a guess about which
   part was wrong; on 503 (directory down) it says the directory could not be reached and that
   local accounts still work; on 429 it says to wait. After login it goes to the page the user
   was sent from (a safe, same-origin path only) or `/`. No marketing copy, no logo art.
2. **401 anywhere → `/login`**, keeping the path to return to. The shared fetch helpers in
   `frontend/src/lib/api.ts` send the cookie (`credentials: "same-origin"`; in development the API
   is on another port, so use `"include"` there, and say how you decide). No token in
   `localStorage`/`sessionStorage`, ever.
3. **The shell**: the top bar shows who is signed in (display name or username) and a **Sign out**
   control (`POST /auth/logout`, then `/login`). In development with the `local-dev` principal it
   shows that name and no Sign out.
4. **`must_change_password`**: the app shows only a password-change page (current, new,
   confirm; the backend's rules and 422 reasons shown as sent) until it succeeds.
5. **No role yet**: a user whose `/me` has no permissions sees one page that says their account
   exists and has no role, and that an administrator assigns roles under Administration → Users.
   No navigation to pages they cannot read.
6. **Administration → Users** (`admin:users`): a table (username, source LOCAL/LDAP, display
   name, roles, status active/disabled/locked, last login with UTC), and actions: create a local
   user (initial password, shown once, user must change it), pre-create an LDAP user, set roles
   (a picker over the roles the backend lists), disable/enable, unlock, reset a local password.
   The last-admin 409 is shown as the backend says it. Every action says what it did.
7. **Administration → Directory (LDAP)** (`admin:auth`): the form for the `ldap_config` row
   (enabled, URLs in order, StartTLS, CA PEM text area, bind template with a preview of the
   rendered bind name for a typed username, timeout, optional user search base), saved with its
   `version` (409 on a stale version says someone else changed it and offers a reload; 422 lists
   every reason). A **Test connection** panel takes a test username and password (never stored,
   cleared after the test) and shows the TLS result, the certificate's subject, issuer and expiry
   (UTC), the bind result and the latency, each as sent by the backend.
8. **Roles list for the picker:** if no route lists the roles, add exactly one read-only backend
   route `GET /api/v1/users/roles` (`admin:users`) returning each role's name and permissions,
   one statement, with tests (shape, 401/403 via the OpenAPI-driven guard test). Any other backend
   change: stop and report.
9. **Navigation**: Users and Directory under Administration, shown only to callers holding the
   permission. `frontend/src/widgets/permissions.ts` must stay in sync with `rbac.py` (its
   contract test).

## States and tests

Every state designed and tested (AGENTS.md §3a): login (idle, submitting, 401, 503, 429), the
redirect on 401 with the return path (and an unsafe return path refused), Sign out, the
must-change gate, no-role, the users table (empty, error, forbidden), each user action and its
error, the LDAP form (no row yet, saved, 409, 422 list) and the test panel (TLS failed with
reason, TLS ok + bind invalid credentials, success). Vitest for each; break-it each new test.

## Screenshots

`/login` (idle, 401), the must-change page, the no-role page, Administration → Users (with LOCAL
and LDAP users, a locked and a disabled one), Directory (saved config, a failed TLS test, a
successful test) at 1440×900, 1280×800 and 1024×768, against a deterministic fixture that serves
the real AUTH-1 shapes (and sets a session cookie). Fixture, script, PNGs and scratch under
`/home/efe/Documents/QRadar-Dash-work/auth2/`. Ports 3102/8102/9102. Open the PNGs, say what you
saw, list their paths.

## Allowed files

- `frontend/src/app/login/**`, `frontend/src/app/admin/users/**`, `frontend/src/app/admin/directory/**`
  (or the route names you choose under `admin/`; say which), and a password-change route
- `frontend/src/components/shell/**`, `frontend/src/lib/api.ts`, `frontend/src/lib/nav.ts`,
  `frontend/src/widgets/permissions.ts`, a new `frontend/src/components/auth/**`
- `frontend/src/app/layout.tsx` and the root providers only as needed for the session state
- `backend/app/api/routes/users.py` and `backend/app/schemas/users.py` for item 8 only, with tests
- new tests; `docs/tasks/AUTH-2.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. If item 8 was built: backend ruff, mypy 0, unit,
the users integration tests on the `TEST_DATABASE_URL` your prompt gives. Copy each summary line
verbatim (AGENTS.md §6). Stop every process you start by PID. Do not commit. Nothing under `/tmp`.
