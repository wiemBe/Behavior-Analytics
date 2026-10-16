# Task AUTH-1 — production login: local users + LDAP bind, sessions, user and role administration (backend)

Read `AGENTS.md` at the repository root first and follow it exactly; §2 rules 5 (no secrets in
source, logs, responses or the browser), 7 (additive migrations) and 8 (fail closed) matter most.
Then read `README.md` "Security model", `backend/app/security/auth.py`, `backend/app/security/
rbac.py`, `backend/app/models/identity.py`, `backend/app/services/seed.py`, `backend/app/main.py`
(CORS, middleware) and `backend/app/core/config.py` (the auth and production checks).

## Why

In production `get_principal` refuses local auth and the OIDC branch returns 501, so **the
application cannot be used in production today.** The user decided (PLANNER D-19, 2026-10-16):

- users log in with **local accounts** or with their **Active Directory password over LDAP**;
- **no group or role is read from AD**: roles are assigned inside the application, by users who
  hold the admin role;
- the operator is the user; the deadline is about two weeks (D-18).

OIDC stays out of scope (it remains refused in production, with a clear startup error).

## Design (binding; deviate only by stopping and reporting)

1. **Accounts** (`app_user`, additive migration):
   - `username` (unique, case-insensitive, normalised to lower case), `auth_source`
     (`LOCAL` | `LDAP`), `hashed_password` (LOCAL only, **bcrypt** via the installed `bcrypt`
     package, cost ≥ 12; never passlib), `must_change_password`, `failed_login_count`,
     `locked_until`, `disabled_at`. Keep `email` but allow it to be NULL for LDAP users whose
     directory entry has no `mail` (say how you handle the existing NOT NULL + unique: an
     additive migration that relaxes it is acceptable; explain the downgrade).
   - **A new LDAP user has no role** and therefore no permission. `/me` works for them and says
     so; every other guarded route is 403 (fail closed).
   - An admin may pre-create an LDAP user by username (no password), so roles exist before the
     first login. The first successful LDAP bind of an unknown username creates the user
     (just-in-time), with no role, and audits `user.provision`.
2. **LDAP** (`ldap3`, already installed in the venv; add `ldap3>=2.9.1` to `pyproject.toml`).
   **The user wants the LDAP connection entered and changed from the UI (2026-10-16 ~11:33),
   not from environment variables, and LDAPS supported.** So:
   - the configuration lives in the **database**, one row (a new `ldap_config` table in the same
     migration): `enabled` (false), `url` (`ldaps://host:636`, or `ldap://host:389` only with
     `start_tls=true`; several URLs allowed, tried in order, for redundant domain controllers),
     `start_tls`, `ca_pem` (the CA certificate chain as PEM text: public material, not a secret;
     validated on save: parses as X.509, not expired, and say what you check),
     `bind_template` (e.g. `{username}@corp.example`, the AD UPN form, or a DN template with
     `{username}`), `timeout_seconds` (default 5, 1–30), optional `user_search_base` for reading
     the user's own `displayName`/`mail` after the bind, `version` for optimistic concurrency,
     `updated_by`, `updated_at`.
   - **No password is stored**: authentication binds as the user. If a service account is ever
     needed, that is a later task; do not add one.
   - API (permission `admin:auth`, satisfied by `admin:*`; not by `read:*`):
     `GET /api/v1/auth/ldap-config`, `PUT /api/v1/auth/ldap-config` (carries `version`; 409 on a
     stale version; 422 lists every reason), and `POST /api/v1/auth/ldap-config/test` which takes
     a draft config **plus a test username and password typed by the admin**, performs the TLS
     handshake and a bind, and answers `{tls: ok|failed + reason, certificate: subject, issuer,
     not_after, bind: ok|invalid_credentials|failed + reason, latency_ms}`. The test password is
     never stored, logged or echoed. Every config change is audited (`auth.ldap_config.update`,
     before/after without any credential).
   - TLS verification is always on (the project's rule): the server certificate must chain to
     `ca_pem` (or to the system trust store when `ca_pem` is empty, saying so in the test
     result) and match the host name. `ldap://` without StartTLS is refused at save time in
     every environment.
   - `ldap_enabled=false` (the default) means LDAP logins are refused with a clear reason, and
     local logins work.
   - authenticate by **binding as the user** with the password they typed. **Never read
     `memberOf` or any group.** Reading the user's own `displayName` and `mail` after a
     successful bind is allowed.
   - escape the username for the DN/UPN (RFC 4514) and refuse empty passwords (an empty
     password is an anonymous bind on many servers: it must never authenticate).
   - the LDAP call is blocking: run it off the event loop.
3. **Sessions**: server-side, in a new `auth_session` table: a random 256-bit token, **stored
   only as its SHA-256 hash**, `user_id`, `created_at`, `last_seen_at`, `expires_at`, idle
   timeout (`session_idle_minutes`, default 480) and absolute lifetime
   (`session_max_hours`, default 12). The cookie `qd_session`: `HttpOnly`, `SameSite=Strict`,
   `Secure` in production, `Path=/`. Logout deletes the row. Disabling a user or changing their
   roles takes effect on the next request (permissions are read per request, not stored in the
   session). A bounded cleanup of expired sessions (a beat task or on login).
4. **CSRF**: `SameSite=Strict` plus, for every unsafe method, an `Origin` (or `Referer`) check
   against the configured public origin; refuse with 403 and a clear reason otherwise.
5. **Login and lockout**: `POST /api/v1/auth/login {username, password}`. A local account with
   that username authenticates locally; otherwise, if LDAP is enabled, LDAP. The response never
   says which part was wrong ("Invalid username or password"). 5 consecutive failures lock the
   username for 15 minutes (settings), plus a per-IP rate limit on the route (`slowapi`).
   Constant-time comparison; a dummy bcrypt check for unknown local usernames. Audit
   `auth.login`, `auth.login_failed` (username, never the password), `auth.logout`,
   `auth.locked`.
6. **Password change**: `POST /api/v1/auth/password` for LOCAL users (current + new, min 12
   characters, not equal to the username). `must_change_password` users may call only this,
   `/me` and logout until they change it.
7. **`get_principal`**: in production (and whenever `auth_provider="session"`), the principal
   comes from the session cookie; no cookie or an invalid/expired session is 401. The dev
   convenience (`local-dev` with `admin:*` when not production and no session) stays for
   development **only** when `auth_provider="local"` and not production, as today, so the
   existing test suite keeps working. `/me` gains `username`, `auth_source`,
   `must_change_password` and `roles`.
8. **User administration** (new permission `PERM_USER_ADMIN = "admin:users"`, satisfied by
   `admin:*`): list users (username, source, display name, roles, active/disabled, last login,
   locked), create a LOCAL user (initial password, `must_change_password=true`), pre-create an
   LDAP user, set roles (by role name; unknown role 422), disable/enable, reset a LOCAL user's
   password (sets `must_change_password`), unlock. Every write audited with before/after
   **without** password material. **Guard: the last active user holding `admin:*` can be neither
   disabled nor stripped of the admin role** (409, saying why).
9. **Break-glass admin**: a CLI `python -m app.cli.users create-admin --username <u>` that
   creates (or resets) a LOCAL admin with a generated password printed **once** to the
   terminal and `must_change_password=true`. The installer (OPS) will call it. The existing
   seed's admin user: say what you do with it (it must not end up as a passwordless admin in
   production).
10. **CORS**: in development the frontend (3000) calls the API (8000) with credentials; keep
    `allow_credentials=True` with the explicit origin. Production is one origin behind the
    proxy.

## Tests

- Unit: bcrypt hashing and verify; the lockout counter; the username escaping (DN and UPN with
  `*`, `(`, `\`, `,`, NUL); the empty-password refusal; the session token hashing; the CSRF
  origin check; the LDAP config validators (`ldap://` without StartTLS refused, a bad or
  expired PEM refused, the template must contain `{username}`).
- Integration for the LDAP config API: read, update with version (409 on stale), 422 reasons,
  403 without `admin:auth`, the test endpoint with a fake server (TLS failure, certificate
  details, invalid credentials, success), the test password absent from every log line and
  response, the audit row.
- Integration (your DB): login local OK/wrong password/unknown user (same message, same status);
  lockout after 5 and unlock after the window; session cookie flags; expired and idle sessions
  401; logout; disabled user 401 on the next request; role change effective on the next request;
  LDAP login with a **fake LDAP** (patch the ldap3 connection: success, invalid credentials,
  server down → 503 with a reason, never "valid"); JIT provisioning with no role → 403 on a
  guarded route and 200 on `/me`; the last-admin guard; audit rows; no password or hash in any
  response or log line (assert on captured logs); the `must_change_password` gate.
- Every route's 401/403 (drive it off the OpenAPI document, as `test_phase3_api.py` does).
- Break-it every new test and report which failed.

## Migration

One additive migration, `0025_auth_sessions`. **Set its `down_revision` to `"0023"` for now**:
F-1's `0024` is not merged yet; the orchestrator re-chains it to `0024` at merge time. Upgrade,
`alembic check`, downgrade, upgrade, `alembic check` on your migration DB.

## Not in this task

The login page and the user-administration UI (AUTH-2), OIDC, reading AD groups, the installer.

## Gates (from `backend/`, `PYTHONPATH=.`, the main checkout's venv)

- `ruff check app tests` clean; `mypy app` **0 errors**.
- Unit: `ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= pytest -m "not integration"`.
- Integration only on the `TEST_DATABASE_URL` your prompt gives; new files first, then the whole
  integration suite once. Alembic round trip on the migration DB your prompt gives. `addopts`
  already has `-q`; do not add another.

Write `docs/tasks/AUTH-1.report.md` (AGENTS.md §6) as you go, with the summary lines verbatim.
Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/auth1/`, never `/tmp`.
