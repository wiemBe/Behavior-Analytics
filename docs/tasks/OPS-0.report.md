# OPS-0 report

Design only. No code, no commit, nothing run against QRadar.

The bundled design-skill loop was not used. It tells the coordinator not to write the document, it allows scratch under `/tmp`, and it stops to ask a person. This task names exactly two repository files, forbids `/tmp`, and this session has no person to ask. The user's decisions in the task (D-16, D-18, D-19, LDAP from the UI, private-shaped GHCR) are written as binding.

## 1. Files changed

- `docs/OPERATIONS-DESIGN.md` — the design: versioning, the GHCR release, `./qradar-dash install`, upgrade, backup/restore/rollback, `doctor`, `support-bundle`, the Turkish runbook outline with 42 symptoms, what to watch, the OPS-1…OPS-10 split through 2026-10-30, and three open questions.
- `docs/tasks/OPS-0.report.md` — this report.

No other repository file was edited. `docs/PLANNER.md` and `AGENTS.md` were not touched.

## 2. What this meets

Citations are against this branch. Migration head is `0025` (`backend/alembic/versions/0025_auth_sessions.py:28-29`). AUTH-1, AUTH-2 and Phase F are in the tree. The frontend lock pins `postcss` `8.5.30` (`frontend/package.json:35-37`).

1. **Versioning.** One `VERSION` file. `0.1.0` is copied today in `backend/pyproject.toml:3`, `frontend/package.json:3`, and `FastAPI(version="0.1.0")` at `backend/app/main.py:63`. OPS-1 makes those read `VERSION`. Images get the version and git SHA as build args and OCI labels. `GET /api/v1/version` is a new unauthenticated route beside health, not a change to `/live` or `/ready`. The account menu and the sign-in page show it. Release notes live in `docs/releases/<version>.md` with `min_from`, copied into the backend image, and `upgrade` reads them from that image rather than from the GitHub API. No `latest` tag.

2. **Release pipeline.** `.github/workflows/release.yml` on a `v*` tag matching `VERSION`. Gates are stricter than today's CI (`mypy app || true` at `.github/workflows/ci.yml:48`, `npm install` at `:84`). Images: backend, frontend, postgres, pinned qradar-mcp, the new proxy, and Redis pinned by digest. Migrate, the worker and beat are the backend image. One frontend image: `NEXT_PUBLIC_API_BASE_URL=/api/v1` at build, because that value is inlined (`frontend/src/lib/api.ts:6` falls back to localhost). Lock proposal is pip-tools with hashes, not uv. Private registry: `docker login` from `.secrets/ghcr.token` (`read:packages`), plus `--offline` and `--build`.

3. **Install.** Preflight covers Docker, Compose, CPU, RAM, disk, ports, clock, and SELinux (read `getenforce`, never change it; secrets stay on the `load-secrets.sh` path). The wizard does not ask for LDAP. It generates `ENCRYPTION_KEY` inside the backend image (Fernet, not `openssl rand -base64`) and the DB password, and it puts the QRadar token and CA through `load-secrets.sh`. Default TLS is a bundled nginx image on a pinned edge address so `FORWARDED_ALLOW_IPS` is stable. Order: pull, postgres and redis, migrate, `create-admin` once, `qradar add` inside the container, start, health. **`python -m app.services.seed` is not called** — on an empty database it inserts a mock instance. Re-run does not rotate keys and does not call `create-admin` again (`has-admin` is new work in OPS-4; today every `create-admin` resets the password, `backend/app/cli/users.py:59-69`). `--build` is the local fallback.

4. **Upgrade.** Preflight reads `min_from` from the target image. Pull while writers are still up. Stop writers, Timescale-aware `pg_dump`, flush Redis (it is not the system of record), migrate, start, health. Automatic rollback on migrate failure, health timeout, version mismatch, or `doctor` database/migration FAIL. A QRadar or LDAP failure does not roll the database back. Expected downtime is dump-dominated; the design budgets about 10 minutes under 5 GiB and tells OPS-6 to measure it rather than promise it.

5. **Rollback, backup, restore.** A backup is the custom-format dump, `env.bak`, and a mode-0600 tar of the secrets volume and the proxy cert volume. Restore uses `timescaledb_pre_restore` / `pg_restore --no-owner` / `timescaledb_post_restore` on a database that already has the extension. Pre-upgrade dumps are restored onto a scratch database and checked before migrate. Disk guard: free space at least about twice `pg_database_size` plus 2 GiB. Retention: last 7 backups, and pre-upgrade dumps until two newer successful upgrades exist. Rollback restores into a side database, checks it, then renames. It does not run `alembic downgrade` (`README.md:704-706`).

6. **Doctor.** Host half for `df`, NTP and SELinux; `python -m app.cli.doctor` inside the backend for the rest. Each line is PASS, WARN or FAIL, one cause, and a runbook id. QRadar check is `GET /system/about` plus a TLS handshake that sends no token. No Ariel search, no LDAP login, no write. Beat liveness is a Redis heartbeat, because the Compose check only sees `/tmp/celerybeat-schedule`. Disk free space is a host check: the backend container does not mount the Postgres volume.

7. **Support bundle.** Mode 0600. Logs, doctor output, config with secrets removed, versions, migration state. No database dump and no `.secrets/`. Redaction is an explicit denylist plus exact-value scrub, not `app.security.redaction` (its `sec` substring already matches unrelated keys). The fixture plants a token, a Fernet key, a password, a webhook header and a private key, and it requires a `correlation_id` to survive.

8. **Runbook outline.** `docs/OPERATIONS.md` is OPS-9, in Turkish, under Kurulum, Dizin, Yükseltme, Yedek ve geri alma, Tanı, Belirtiler. Commands stay English. §8 lists OPS-R1 through OPS-R42, each with symptom, cause, confirm command and fix, drawn from this tree (QRadar 401/403, CA and AKI, Ariel timeout and truncation, disabled families, unvalidated WAF/IPS profiles, a beat that only looks healthy, migration head, the one-origin CORS mistake, SELinux, a full disk, clock step, and the rest).

9. **Monitoring.** §9 says what the UI already shows (health, the connection pill, collection lateness, the account menu) and a five-step daily watch. The top-bar collection signal is only `PHASE_A_VOLUME`. No Prometheus in this cut.

10. **Task split.** OPS-1 through OPS-10, 2026-10-17 to 2026-10-30. Safety first: version and lock, release images, install, backup and real retention (`apply_policies` is not called by the running process today), upgrade/rollback, doctor, support bundle, the Turkish runbook, then the fresh-VM install test (OPS-10, orchestrator, counts commands and minutes). Signing, Redis AUTH, baking the MCP profile, off-host backup copy, an exporter, and `ALTER ROLE` are OPS-11…OPS-16, after 2026-10-30.

11. **Open questions.** Three, in §11, each with the default the tasks will use. Language, operator, LDAP-from-UI, and public-versus-private were not re-asked.

Host size in §3.1: minimum 4 vCPU, 8 GiB RAM, 80 GiB disk. Recommended 8 vCPU, 16 GiB RAM, 400 GiB for data and 400 GiB for backups on another filesystem. The 1 KiB/row figure is a planning constant, not a measurement. The installer writes 90 days of Timescale retention for the tables `apply_policies` covers, so a small disk can boot; 400 days is the seasonal recommendation and is one `.env` change. WAF, IPS, rule-match and custom rules stay off at install. Catch-all and geo stay at their code defaults (on).

## 3. Gates

No application code, so these were not run: `ruff check`, `mypy app`, pytest, frontend lint, typecheck, vitest, Docker, Compose.

Not run against QRadar. No `git commit`.

A local read of the design's `` `path:line` `` citations found 232 citations whose files exist and whose line numbers fall inside those files. Two short names (`users.py`, `profile.py`) matched more than one file; those citations now use `backend/app/cli/users.py` and `backend/app/waf/profile.py`.

Corrections made after that read, so the document does not claim behaviour the tree does not have:

- The frontend health probe returns only `{status: "ok"}` (`frontend/src/app/api/health/route.ts:1-3`). OPS-1 adds the `version` field. The earlier sentence said the probe already returned it.
- `search_failure_alert_after` (`config.py:145`) counts failed runs of one scheduled search. `doctor`'s `errors_recent` is a count of watermark rows and does not read that setting.
- The custom-rule and baseline-lookback rows in the sizing table were short one column.
- The rate limiter cites `rate_limit.py:16` (`key_func=get_remote_address`). The README is what calls that `request.client.host` (`README.md:635-638`).
- An LDAP user with no roles is created at `auth_login.py:257-265`. `User.roles` is `identity.py:98-100`.
- Symptom ids are OPS-R1…OPS-R42, the same ids `doctor` prints and OPS-9 must cover.
- `apply_policies` is defined at `timescale.py:49`. The only call site in the tree is a test (`backend/tests/integration/test_db_constraints.py:105-108`).

## 4. Open points

- §11 question 1: first GHCR tag stays `0.1.0` unless someone sets `VERSION` to `1.0.0` before the tag. Default in the design: `0.1.0`.
- §11 question 2: bundled nginx is the default TLS terminator. `--external-proxy` is the other case. Default: bundled proxy.
- §11 question 3: backups in this cut stay on a directory on the host. A copy to another machine is OPS-14, after 2026-10-30.
- Bytes per row, RSS, and upgrade minutes are unmeasured. OPS-6 records downtime. `doctor` prints `pg_database_size` so the 1 KiB constant can be replaced.
- `has-admin`, the beat Redis heartbeat, the version route, the lockfile, and the release workflow do not exist yet. They are OPS-1, OPS-2, OPS-4 and OPS-7.
- The shipped backend image is Python 3.12, so the missing-AKI failure is latent until the base image moves or someone runs the backend outside the image.
- Same-disk backups warn and do not fail install. A second filesystem is the recommendation, not a hard preflight failure.
- UI acceptance screenshots do not apply: this task writes no UI.
