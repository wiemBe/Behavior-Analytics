# OPS-7 report

## Files changed

- `backend/app/cli/doctor.py` — added the read-only in-container doctor command, all 19
  required checks, text and JSON renderers, redaction, runbook references, and exit codes.
- `backend/app/workers/celery_app.py` — added the additive 60-second beat-heartbeat schedule
  and the shared key/180-second TTL constants.
- `backend/app/workers/tasks.py` — added the heartbeat task, which writes only a UTC timestamp
  to Redis with the bounded TTL.
- `backend/tests/unit/test_doctor.py` — added forced PASS/WARN/FAIL coverage for the doctor
  checks, redaction fixtures, the exit-code matrix, and a guard that permits only
  `validate_connection` plus provider cleanup.
- `backend/tests/unit/test_beat_heartbeat.py` — pinned heartbeat registration, timestamp-only
  payload, key, and TTL.
- `backend/tests/integration/test_doctor.py` — migrated the assigned database and exercised
  `db_connect`, equal/forced-mismatched `migration_head`, `timescale`, `watermarks`, and
  `cadence` against real rows.
- `docs/tasks/OPS-7.report.md` — this report.

## What I did

- Implemented every in-container check from design §6: `db_connect`, `migration_head`,
  `timescale`, `retention_policy`, `redis`, `celery_workers`, `celery_beat`, `qradar_tls`,
  `qradar_auth`, `qradar_clock`, `watermarks`, `cadence`, `families_off`, `errors_recent`,
  `pg_database_size`, `encryption`, `secrets_files`, `public_origin`, and `version_match`.
- Read the image migration head through Alembic `ScriptDirectory`; there is no hard-coded
  revision.
- Kept QRadar access read-only. The authenticated check calls only `validate_connection`
  (`GET /system/about`). The separate TLS probe uses the configured verifying context and no
  `SEC` header. Tests replace the TLS edge and never open a QRadar socket.
- Added filtered-then-unfiltered Celery inspect behavior so a hostname mismatch is WARN with
  `OPS-R41`, while no pong is FAIL.
- Made the host-only NTP and filesystem-free-space limitations explicit in-container.
  `qradar_clock` is the design-mandated WARN; `pg_database_size` reports the growing database
  quantity while naming the unavailable host free-space check.
- Added `--json` with the same statuses/messages and redaction as text output. A fixture plants
  a token, password, credential-bearing DSN, and Fernet-shaped ciphertext and proves none is
  emitted.
- Deliberately broke and restored four safeguards. The targeted tests failed when the heartbeat
  TTL was changed to 179, JSON redaction was removed, exit codes were forced to zero, and
  `validate_connection` was replaced by another provider call.

## Gates

- `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/ruff check app tests`
  — `All checks passed!`
- `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/mypy app`
  — `Success: no issues found in 241 source files`
- `PYTHONPATH=. ENCRYPTION_KEY=... /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -m "not integration"`
  — `3468 passed, 9 skipped, 1287 deselected, 5 warnings in 64.65s (0:01:04)`
- `ENCRYPTION_KEY=... PYTHONPATH=. TEST_DATABASE_URL=<assigned OPS-7 URL> /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -m integration tests/integration/test_doctor.py`
  — `1 passed, 1 warning in 1.04s`
- `ENCRYPTION_KEY=... PYTHONPATH=. TEST_DATABASE_URL=<assigned OPS-7 URL> /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -q -m integration tests/integration/`
  — completed at `[100%]`, exit 0. The repository's configured `-q` plus the command's `-q`
  suppressed pytest's count line; collect-only selected 1,287 integration tests, and the run
  showed no skips or failures. Warnings were dependency deprecations plus existing Pydantic
  serializer warnings.
- `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/python -m app.cli.doctor --help`
  — exit 0; displayed the `--json` option.
- `git diff --check` — clean.

## Open points

- The host-side `./qradar-dash doctor` checks are explicitly outside OPS-7's assigned scope.
- `qradar_clock` intentionally keeps an in-container doctor at WARN until the host wrapper
  supplies NTP status; `/system/about` has no server time.
- Until OPS-1 is present in a merged image, `version_match` reports a missing version field or
  backend version route as WARN and never invents a version. An unreachable frontend probe is
  FAIL as required.
- The full suites reported only pre-existing/dependency warnings; no test failed.

## Round 1

### What changed

- `celery_workers` now sends one unfiltered `inspect ping`. At least one response is PASS and
  reports the worker count and sorted, redacted node names; no response is FAIL with `OPS-R41`.
  The backend-container hostname probe and its unavoidable production WARN were removed.
- Removed `qradar_clock` from the in-container doctor method, run list, and tests. The module
  docstring now states that `qradar_clock` and disk free space belong to the host-side
  `./qradar-dash doctor`. An all-PASS fake run proves the in-container command can exit 0.
- Added one `CHECK_TIMEOUT_SECONDS = 10.0` limit around every check in `Doctor.run()`. A timeout
  becomes a FAIL for that check id with its normal runbook id, and later checks continue.
- Changed missing and stale `celery_beat` messages to say beat may not be dispatching or no worker
  may be running its tasks. The 180-second threshold is unchanged.
- Broke and restored each new guard: changing timeout handling to WARN failed the timeout test;
  changing no-pong to WARN failed the worker test; changing all-PASS exit status to 1 failed the
  healthy-run test.

### Gates

- `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/ruff check app tests`
  — `All checks passed!`
- `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/mypy app`
  — `Success: no issues found in 241 source files`
- `PYTHONPATH=. ENCRYPTION_KEY=... /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -m "not integration"`
  — `3469 passed, 9 skipped, 1287 deselected, 5 warnings in 57.50s`
- `ENCRYPTION_KEY=... PYTHONPATH=. TEST_DATABASE_URL=<assigned OPS-7 URL> /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -m integration tests/integration/test_doctor.py`
  — `1 passed, 1 warning in 0.96s`

### Open points

- The host wrapper remains outside OPS-7's allowed files and will own the removed
  `qradar_clock` line plus disk free-space reporting.
- The test warnings are unchanged dependency deprecations; the focused integration warning is
  Alembic's existing `path_separator` deprecation.
