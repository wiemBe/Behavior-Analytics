# Task OPS-7 — `python -m app.cli.doctor`: the in-container diagnostics, and a beat heartbeat

Read `AGENTS.md` at the repository root first and follow it exactly. §2 rule 1 (read-only against
QRadar) and rule 5 (no secrets anywhere) matter most. Then read `docs/OPERATIONS-DESIGN.md`:
- **§6**, which is binding: the check table, the line format, the exit codes, and what `doctor`
  never does;
- §8, the runbook ids `OPS-R<n>` the lines point to;
- §10.2's OPS-7 line.

## Scope

**This task: the in-container half.**
- `backend/app/cli/doctor.py`, run as `python -m app.cli.doctor`.
- The beat heartbeat.

**Not this task: the host half.** The `./qradar-dash doctor` wrapper (host `df`,
`timedatectl`, `getenforce`, `docker compose ps`) is added to the operator script after OPS-4
merges. Print a clear note when a host-only check is not available in-container.

## Build

1. **Every in-container check in design §6's table:**
   `db_connect`, `migration_head`, `timescale`, `retention_policy`, `redis`, `celery_workers`,
   `celery_beat`, `qradar_tls`, `qradar_auth`, `qradar_clock` (skipped, as the design says),
   `watermarks`, `cadence`, `families_off`, `errors_recent`, `pg_database_size` (the in-container
   part of `disk`), `encryption`, `secrets_files`, `public_origin` and `version_match`.
   - **`migration_head`:** compare the live `alembic_version` with the head of the Alembic
     script directory shipped in the image. Read it with Alembic's `ScriptDirectory`, not with
     a hard-coded value. OPS-1 (in flight) adds a version module that may expose the same thing;
     do not depend on it.
   - **`version_match`:** read the frontend probe (`/api/health` on the frontend service) and
     `GET /api/v1/version` (OPS-1, in flight; the shape is in design §1.2) over HTTP inside the
     compose network. When either is unreachable or the route does not exist yet, print what
     the design says. Never invent a version.
   - **`retention_policy`:** read `timescaledb_information.jobs` as the design says. OPS-5a (in
     flight) wires `apply_policies`; this check reports on whatever policies exist.
2. **Line format, exit codes and runbook ids** exactly as in §6. **No token, `.env` value, DSN,
   password, `ENCRYPTION_KEY` or ciphertext** in any line.
   - Connection errors name the host and the exception class.
   - Reuse the app's redaction helpers (`app/security/redaction.py`) for any free text that
     comes from outside.
3. **The QRadar checks.**
   - The only QRadar API call is `validate_connection` (`GET /system/about`) through the
     read-only provider.
   - The TLS check is a handshake with the configured CA that sends no `SEC` header.
   - No Ariel search, no sync, no LDAP bind, no write. Add a test that fails if `doctor` calls
     anything else on the provider.
4. **The beat heartbeat (§6, last paragraphs).**
   - A 60 s beat task sets a Redis key with a 180 s TTL. The value is a timestamp only.
   - `celery_beat` reads that key.
   - Add the task in the style of the existing beat entries in `backend/app/workers/`.
   - OPS-5a is also adding a beat task in those files. Keep your change additive so the merge
     is simple.
5. **`--json`** (optional, if cheap): the same results as JSON for OPS-8's support bundle. The
   same redaction applies.

## Tests (§10.2 OPS-7)

- For **every** check id: a test that forces PASS and a test that forces WARN or FAIL. Use
  fakes for Redis, Celery inspect, the provider and HTTP. **No test opens a socket to a QRadar
  host.**
- Output fixtures with a **planted token, password and DSN**: the test asserts that none appears
  in the output.
- The exit-code matrix: all PASS gives 0, any WARN with no FAIL gives 1, any FAIL gives 2.
- An integration test on your own DB: `db_connect`, `migration_head` (equal, then a forced
  mismatch), `timescale`, `watermarks` and `cadence` against real rows.
- Break each new guard once, then restore it.

## Gates

From `backend/`, with `PYTHONPATH=.` and the main checkout's venv:
- `ruff check app tests`, clean;
- `mypy app`, 0 errors;
- the unit tests;
- integration on `postgresql+asyncpg://test:test@localhost:5433/qradar_obs_ops7`: your new files,
  then `tests/integration/` in full, once.

Copy each summary line verbatim.

## Allowed files

- `backend/app/cli/doctor.py` (new), and a small `backend/app/cli/doctor_checks/` package if
  that reads better
- `backend/app/workers/celery_app.py` and `tasks.py` (the heartbeat only)
- `backend/app/core/config.py` (a heartbeat key or TTL setting only, if needed)
- new tests
- `docs/tasks/OPS-7.report.md` (new)

Do not commit. Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops7/`, never `/tmp`.
Nothing runs against a real QRadar.
