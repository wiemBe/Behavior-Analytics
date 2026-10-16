# AUTH-1 report

Status: complete. Production sign-in is a server-side session. The login page and
the user-administration UI are AUTH-2 and were not built.

## Files changed

- `backend/alembic/versions/0025_auth_sessions.py` — additive migration, `down_revision = "0023"`. Account columns, `ldap_config` (one row, no sequence on `id`), `auth_session`. Disables a pre-existing passwordless `admin@example.internal` and leaves its username NULL. Downgrade restores that row and fills NULL emails with `{id}@unspecified.invalid` before setting email NOT NULL.
- `backend/app/models/enums.py` — `AuthSource` (`LOCAL`, `LDAP`).
- `backend/app/models/identity.py` — account columns and checks; `AuthSession`; `LdapConfig`. Email is nullable.
- `backend/app/models/__init__.py` — exports the two new models.
- `backend/app/core/config.py` — `auth_provider` includes `session`. Session, lockout, public-origin and login-rate settings. Production requires an https `PUBLIC_ORIGIN` and refuses `AUTH_PROVIDER=oidc`. It does not require `auth_provider=session`.
- `backend/app/security/passwords.py` — bcrypt cost 12 via the `bcrypt` package. Missing and over-long passwords still run a dummy check and return false.
- `backend/app/security/usernames.py` — username shape: reject controls before strip, then lower-case, no internal whitespace, length 1–255.
- `backend/app/security/ldap_names.py` — RFC-style escape and `{username}` substitution via `str.replace` only.
- `backend/app/security/csrf.py` — Origin, else Referer, against the public origin. Only when session cookies are enforced.
- `backend/app/security/rbac.py` — principal carries username, auth source, must-change, roles and user id. `admin:users` and `admin:auth`. `admin:*` grants them; `read:*` does not.
- `backend/app/security/auth.py` — session cookie is the principal in production and whenever `auth_provider=session`. The database is opened only on that branch, in its own transaction, honouring `get_session` overrides. Dev `local` and the existing oidc path are unchanged.
- `backend/app/services/auth_sessions.py` — 256-bit token stored as SHA-256 hex, idle and absolute expiry, bounded cleanup.
- `backend/app/services/auth_login.py` — local accounts never fall through to LDAP. Lockout, generic failures, JIT provision with no role, password change that does not count as a login.
- `backend/app/services/ldap_config.py` — validators collect every reason. Audit snapshot uses `directory_timeout` so the redactor does not mask the timeout.
- `backend/app/services/ldap_bind.py` — user bind off the event loop. TLS verification always on. Empty password never opens a socket. No `memberOf`.
- `backend/app/services/user_admin.py` — create, roles, disable, enable, unlock, reset. Last active `admin:*` holder cannot be removed. Audit flag is `must_change`.
- `backend/app/services/seed.py` — no passwordless admin. `ensure_default_roles` still seeds `_ROLES`. The CLI creates the first administrator.
- `backend/app/repositories/audit.py` — optional `actor_id` and `user_agent`, truncated with the existing redaction.
- `backend/app/schemas/auth.py`, `backend/app/schemas/users.py` — request and response models. Password models hide input in errors. Responses carry no token and no hash.
- `backend/app/schemas/me.py`, `backend/app/api/routes/me.py` — `/me` adds username, auth source, must-change and roles.
- `backend/app/api/routes/auth.py` — login, logout, password change, directory read/update/test.
- `backend/app/api/routes/users.py` — user administration under `admin:users`.
- `backend/app/api/router.py` — mounts the four routers.
- `backend/app/main.py` — CSRF refusal inside the existing security-headers middleware. CORS stays credentialed, one public origin, plus `http://localhost:3000` outside production.
- `backend/app/cli/users.py` — `python -m app.cli.users create-admin --username`. Password printed once after commit.
- `backend/pyproject.toml` — `bcrypt` and `ldap3`. `passlib[bcrypt]` is still declared and unused.
- `backend/tests/unit/test_auth.py` — new unit tests.
- `backend/tests/integration/test_auth_api.py`, `backend/tests/integration/ldap_fake.py` — API coverage and a one-bind LDAPS stand-in. Certificates are written under the task scratch directory.
- `backend/tests/unit/test_route_auth_guard.py` — login is open; logout may be called by a read-only principal; new bodies for password, directory and user routes.
- `backend/tests/unit/test_config_and_crypto.py`, `backend/tests/unit/test_tls_and_credentials.py` — production Settings now include `public_origin`.

## What this does

A local account is checked only against its bcrypt hash. Directory sign-in binds as the user over TLS (`ldaps://`, or `ldap://` with StartTLS). `ldap://` without StartTLS is refused at save in every environment. No service account is stored, and groups are not read. A username the directory has never seen is created with no role (`user.provision`): `/me` works and every other guarded route is 403. An administrator can pre-create that username.

Five consecutive failures lock the username for 15 minutes. The HTTP body is always "Invalid username or password", including when the directory is turned off. Directory-down is 503 and does not count toward the lock. The cookie `qd_session` is HttpOnly, SameSite=Strict, Path=/, and Secure in production. Logout deletes the row. Disable and a password reset revoke that user's sessions. A role change is visible on the next request and does not delete sessions. A user with `must_change_password` may call only `/me`, the password route and logout.

`GET/PUT /api/v1/auth/ldap-config` require `admin:auth` and an optimistic `version` (409 when stale, 422 listing every reason). With no row, GET returns version 0 and PUT version 0 inserts version 1. `POST .../ldap-config/test` performs a real handshake and bind and returns the structured result. The test password is not stored, logged or echoed. Every config change is audited as `auth.ldap_config.update`.

The last active user who holds `admin:*` cannot be disabled or stripped of that role (409). The CLI creates or resets a local administrator and prints the generated password once.

## Gates

Commands from `backend/`, venv `/home/efe/Documents/QRadar-Dash/backend/.venv/bin/`, `PYTHONPATH=.`.

- `ruff check app tests` — All checks passed (exit 0). `ruff format` was not run.
- `mypy app` — Success: no issues found in 228 source files (exit 0).
- Unit: `ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= pytest -q -m "not integration"` — exit 0. Progress marks: 3314 passed, 8 skipped, no failures. The redirected run did not print pytest's summary line; the count is the progress glyphs before the warnings summary.
- New integration file, first: `tests/integration/test_auth_api.py` — 17 passed, 1 failed (`test_ldap_config_api_and_probe`). The audit redactor masks any key containing `sec`, so `timeout_seconds` was stored as `***redacted***`. The snapshot key is now `directory_timeout` (and the must-change flag is `must_change`). Re-ran that test: 1 passed.
- Full integration, after that fix and after `ldap_config.id` was given `autoincrement=False`: `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_auth1 pytest -q -m integration` — exit 0, 1215 passed, no failures. An earlier full run failed one existing test, `test_upgrade_from_phase2_preserves_data_and_adds_phase3`, because an integer primary key had grown a sequence default (`ldap_config.id`). That test was re-run alone after the fix: 1 passed.
- Alembic on `qradar_obs_auth1_mig`, after the sequence fix: downgrade base, upgrade head, `alembic check`, downgrade base, upgrade head, `alembic check`. Every step exit 0. Both checks: "No new upgrade operations detected."

Break-it (each mutation restored; the break-it backup matches the tree afterwards):

Unit, each failed as required:

- cost not 12: `test_hash_and_verify_round_trip`
- missing or over-long password accepted: `test_verify_of_a_missing_hash_is_false_and_still_runs`, `test_overlong_password_is_rejected_without_a_fast_path`
- fifth failure does not lock: `test_lockout_starts_on_the_fifth_failure_and_a_success_clears_it`
- elapsed lock kept: `test_an_elapsed_lock_is_cleared_before_the_next_attempt`
- `*` not escaped: `test_username_escape_covers_dn_and_filter_metacharacters`
- raw session token: `test_session_token_is_stored_only_as_its_hash`
- absolute expiry exclusive: `test_session_expiry_includes_the_boundary`
- CSRF never refuses: `test_csrf_refuses_a_missing_or_foreign_origin_only_when_enforced`
- directory reasons ignored: `test_directory_rules_collect_every_reason`, `test_ldaps_refuses_starttls_and_an_expired_certificate`, `test_a_bad_pem_is_refused`
- empty directory config refused: `test_disabled_directory_with_no_urls_is_the_default`
- empty password opens a socket: `test_empty_password_never_opens_a_socket`
- `admin:*` grants nothing: `test_admin_star_satisfies_the_new_permissions_and_read_star_does_not`
- production accepts oidc: `test_production_refuses_oidc_and_a_plaintext_origin`
- directory-down copy contains "valid": `test_directory_unavailable_message_does_not_say_valid`

Integration, each failed as required:

- cookie not HttpOnly: `test_local_login_wrong_and_unknown_share_the_message`
- empty password is a different refusal: `test_empty_password_is_the_same_refusal`
- fifth failure does not lock: `test_lockout_and_unlock_after_the_window`
- expired sessions stay valid: `test_expired_and_idle_sessions_are_rejected`
- disabled account can sign in: `test_logout_and_a_disabled_user`
- role replacement is a no-op: `test_role_change_applies_on_the_next_request`
- must-change gate off: `test_must_change_password_gates_every_other_route`
- last admin can be removed: `test_last_admin_cannot_be_removed`
- unknown roles accepted: `test_unknown_roles_are_listed_and_duplicates_conflict`
- login audit stores a password field: `test_audit_rows_carry_no_password`
- directory-down is 401: `test_ldap_login_paths`
- local accounts fall through to LDAP: `test_a_local_account_is_not_sent_to_the_directory`
- CSRF middleware skipped: `test_cross_site_post_is_refused`
- production cookie not Secure: `test_production_cookie_is_secure`
- stale directory version accepted: `test_ldap_config_api_and_probe`
- break-glass omits the password: `test_break_glass_admin_prints_the_password_once`
- missing cookie is accepted: `test_session_mode_refuses_every_route_without_a_cookie`
- a user with no role receives `admin:*`: `test_a_user_with_no_role_can_read_me_and_nothing_else`

## Open points

- README still describes the security model as OIDC. It was left unchanged, so that section is stale. Production `AUTH_PROVIDER=oidc` is a startup error: "AUTH_PROVIDER=oidc is not available; OIDC is out of scope. Set AUTH_PROVIDER=session".
- Expired sessions are deleted in a bounded batch on login, not by a Celery beat task.
- A saved CA is checked as PEM, not-before, and not-expired, and a private key is refused. `basicConstraints` and chain signatures are not checked.
- `0025` revises `0023` on purpose. F-1's `0024` is not on this branch; the orchestrator re-chains at merge.
- The seed no longer creates `admin@example.internal`. A database that already has that passwordless row is disabled by the migration, and its username stays NULL so it does not occupy `admin`.
- Production with `auth_provider=local` uses session cookies. The old "local auth is disabled" response is only reached if something calls the local branch directly.
- When LDAP is disabled, the HTTP body stays the generic 401. The audit reason is `ldap_disabled`.
- The route-guard allowlist now includes `POST /api/v1/auth/login` (open) and `POST /api/v1/auth/logout` (any authenticated caller).
- `get_principal` does not declare `Depends(get_session)`. Opening a session on the local and oidc paths would point existing tests at the default host `postgres`.
- `passlib[bcrypt]` remains an unused dependency. Hashing goes through `bcrypt`.
