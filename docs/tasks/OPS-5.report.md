# OPS-5 report

## Files changed

- `qradar-dash`: added verified `backup`, guarded `restore`, split restore/swap functions,
  shared disk-guard arithmetic, fail-closed retention, and systemd timer print/install support.
- `backend/tests/unit/test_qradar_dash_backup.py`: added the stub-executable backup, restore,
  retention, timer, cleanup, ordering, mode, secret-leak, and database-drop guard suite.
- `backend/tests/integration/test_backup_restore_roundtrip.py`: added the real
  PostgreSQL/Timescale dump and restore round trip using scratch database names derived from
  `TEST_DATABASE_URL` and a dump under pytest's `tmp_path`.
- `backend/tests/unit/test_qradar_dash_installer.py`: removed only `backup` and `restore` from
  `test_unbuilt_subcommands_exit_non_zero`.
- `docs/OPERATIONS-DESIGN.md`: added OPS-5 as-built notes in §5, including staging, manifest,
  timer, retention, restore swap and encryption-key refusal behaviour.
- `docs/tasks/OPS-5.report.md`: this report.

## What I did

- Backup reads `pg_database_size` and applies the shared `2 × database size + 2 GiB` guard
  before creating anything. `doctor` now uses that same helper. Equality passes.
- A dump is made inside Postgres under its `/tmp`, copied out, removed in-container, and staged
  under `.<name>.partial`. `.env`, inspected named secret volumes, optional state and the
  seven-field manifest are private. The directory is renamed only after verification; traps
  remove the partial, container dump and scratch database on every failure path.
- Verification and restore use the required Timescale sequence. Every database drop goes through
  the live-`POSTGRES_DB` guard. Generated restore/broken database names use a fixed prefix plus a
  lowercase UTC stamp so SQL identifier folding and libpq reconnects agree.
- Retention keeps seven manual/scheduled backups, fails closed for missing/unreadable state,
  protects failed/rolled-back upgrade backups, requires two distinct later successful upgrades,
  rechecks the next-backup guard, and only removes exact non-symlink direct children.
- Restore validates the four required private files and manifest-declared optional state,
  exact version compatibility, keyring membership, disk space and confirmation before stopping
  services. It restarts after pre-swap failures,
  renames the original database back if swap step 5 fails, never drops the broken copy, and does
  not restore secrets automatically.
- `backup --print-timer` is side-effect free. `--install-timer` writes the daily persistent timer
  with a 15-minute randomized delay under the root or user systemd directory and enables it.
- The integration test migrates a derived source scratch database, inserts a role marker and a
  `log_source_metric` hypertable row, streams `pg_dump`/`pg_restore` through the matching test-db
  container without a bind mount, verifies both rows/revision/hypertable, and drops both derived
  databases in `finally`.

## Required guard mutation checks

Each mutation was applied alone, the named test failed, and the production code was restored
before the next mutation.

1. Disk guard changed from `<` to `<=`:
   `test_disk_guard_passes_at_exact_threshold` failed with
   `FAILED tests/unit/test_qradar_dash_backup.py::test_disk_guard_passes_at_exact_threshold`.
2. `guard_drop_database` no longer compared with `POSTGRES_DB`:
   `test_drop_guard_directly_refuses_postgres_db` failed with
   `FAILED tests/unit/test_qradar_dash_backup.py::test_drop_guard_directly_refuses_postgres_db`.
3. Partial-directory removal was replaced with a no-op:
   `test_backup_failures_remove_partial_and_drop_created_scratch[overrides0-dump failed]`
   failed because the `.partial` directory remained.
4. Encryption-key compatibility returned success unconditionally:
   `test_restore_refusals_happen_before_stop_and_do_not_print_keys[args4-None-None-0.1.0-fixture-old-encryption-key-keyring]`
   failed because the mismatched-key restore was allowed.
5. The step-5 rename-back statement was removed:
   `test_restore_step_five_failure_renames_original_back` failed because the recorded SQL lacked
   `ALTER DATABASE qradar_obs_broken_... RENAME TO qradar_obs`.

## Gates

All pytest commands used
`TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops5/tmp`, `PYTHONPATH=.`, the main checkout's
virtualenv, and the supplied `ENCRYPTION_KEY`. Integration commands additionally used the assigned
`TEST_DATABASE_URL`.

- `docker run --rm -i koalaman/shellcheck:stable - < qradar-dash`
  - Final result: no output; exit 0.
- `bash -n qradar-dash && git diff --check`
  - Final result: no output; exit 0.
- From `backend/`: `/home/efe/Documents/QRadar-Dash/backend/.venv/bin/ruff check app tests`
  - `All checks passed!`
- From `backend/`: `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/mypy app`
  - `Success: no issues found in 244 source files`
- From `backend/`: `/home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -o addopts='--strict-markers' -q tests/unit/test_qradar_dash_*.py`
  - `83 passed in 3.53s`
- From `backend/`: `/home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -o addopts='--strict-markers' -q -m 'not integration'`
  - `3586 passed, 10 skipped, 1303 deselected, 5 warnings in 56.63s`
- From `backend/`, integration run 1:
  `/home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -o addopts='--strict-markers' -q -m integration tests/integration/test_backup_restore_roundtrip.py`
  - `1 passed, 1 warning in 1.26s`
- Same integration command, run 2:
  - `1 passed, 1 warning in 1.25s`
- Same integration command, run 3:
  - `1 passed, 1 warning in 1.30s`
- An additional development round-trip before the final three runs also passed:
  - `1 passed, 1 warning in 1.26s`

One combined validation invocation was mistakenly launched from `backend/` with root-relative
`qradar-dash`; it stopped before any gate ran with `bash: qradar-dash: No such file or directory`
(exit 127). The same checks were immediately rerun from the repository root and passed as recorded
above.

## Open points

- The integration runs emit Alembic's existing `No path_separator found in configuration`
  deprecation warning. The round trips pass; changing `alembic.ini` is outside OPS-5's allowed
  files.
- The systemd install path uses a stubbed `systemctl` in tests; no real timer was enabled.
- Per the task decision, backup copies `state.json` only when it exists. Round 1 added its
  explicit manifest declaration so both forms are restorable.
- No command touched the live stack. Real database work was limited to the assigned test server
  and the two scratch databases derived from `qradar_obs_ops5`. No commit was made.

## Round 1

### Review items and tests

1. `state.json` is now declared by `STATE_JSON=included|absent`. Restore always requires
   `database.dump`, `env.bak`, `secrets.tar`, and `MANIFEST`; it conditionally requires state and
   validates any present state as a non-empty regular mode-0600 file. Tests cover an absent state
   round trip, included-but-missing refusal before stop, a missing declaration, and unsafe mode.
2. Backup inspects the QRadar and proxy named volumes before any `docker run -v`. QRadar secrets
   are required; proxy certificates are optional. `SECRETS_VOLUMES` records the included set.
   Tests cover missing required and optional volumes and assert mount calls occur only after a
   successful inspection.
3. Image metadata now comes from `compose ps -a -q` container IDs and each container's Compose
   service/one-off labels plus full `.Image` ID. One-offs and services with no container are
   omitted; a postgres container is required. Tests cover bare-ID normalization, a listed
   `migrate` service with no container, one-off omission, and missing-postgres cleanup.
4. The shared guard now targets both the backup root and Docker root. Separate filesystems use
   the specified independent thresholds; one filesystem uses the summed threshold once. Restore
   checks `2 × size + 2 GiB` on Docker root. Tests cover Docker-root refusal before writes/stops
   and equality at the summed shared-filesystem threshold.
5. Direct restore now requires exact `VERSION` equality. Newer and older messages give their
   respective recovery paths, and both refuse before stop. §5.4 records the as-built deviation.
6. The shared sequence is split into `restore_swap_database` (success leaves services stopped)
   and `restore_start_and_wait`. Direct restore calls both; the direct-function test proves no
   start occurs after a successful swap.
7. A successful swap prints the broken database and exact later drop command before restart and
   readiness. A failed rename-back prints both database names and the exact recovery `ALTER`.
   Tests cover readiness timeout and rename-back failure.
8. A retention guard refusal now names the kept backup. User timer installation explains session
   lifetime, prints `loginctl enable-linger fixture-operator`, and never runs it; `systemctl` is
   stubbed in the test.

### Round 1 guard mutation checks

Each production mutation was applied alone and restored immediately after the named test failed.
The restored focused suite then printed
`.........................................                                [100%]`.

1. State declaration guard mutated to require `state.json` even for `STATE_JSON=absent`:
   `FAILED tests/unit/test_qradar_dash_backup.py::test_backup_without_state_json_is_restorable`.
2. Required-volume inspection bypassed:
   `FAILED tests/unit/test_qradar_dash_backup.py::test_missing_qradar_volume_fails_without_mounting_it`.
3. Required-postgres-container check bypassed:
   `FAILED tests/unit/test_qradar_dash_backup.py::test_missing_postgres_container_removes_partial`.
4. Same-filesystem verified threshold reduced from three copies to two:
   `FAILED tests/unit/test_qradar_dash_backup.py::test_disk_guard_refuses_before_directory_or_dump`.
7. Immediate broken-database instructions removed:
   `FAILED tests/unit/test_qradar_dash_backup.py::test_readiness_failure_still_prints_broken_database_and_drop_command`.

### Round 1 gates

All pytest commands used
`TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops5/tmp`, `PYTHONPATH=.`, the main checkout's
virtualenv, and the supplied `ENCRYPTION_KEY`. Integration commands additionally used only
the assigned `TEST_DATABASE_URL`. Secret values are redacted below per the repository rule.

- `docker run --rm -i koalaman/shellcheck:stable - < qradar-dash`
  - Verbatim result line: no output; exit 0.
- `bash -n qradar-dash && git diff --check`
  - Verbatim result line: no output; exit 0.
- From `backend/`: `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/ruff check app tests`
  - `All checks passed!`
- From `backend/`: `PYTHONPATH=. ENCRYPTION_KEY=<supplied> /home/efe/Documents/QRadar-Dash/backend/.venv/bin/mypy app`
  - `Success: no issues found in 244 source files`
- From `backend/`: `TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops5/tmp PYTHONPATH=. ENCRYPTION_KEY=<supplied> /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -o addopts='--strict-markers' -q tests/unit/test_qradar_dash_*.py`
  - `100 passed in 4.89s`
- From `backend/`: `TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops5/tmp PYTHONPATH=. ENCRYPTION_KEY=<supplied> /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -o addopts='--strict-markers' -q -m 'not integration'`
  - `3603 passed, 10 skipped, 1303 deselected, 5 warnings in 57.59s`
- Integration run 1: `TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops5/tmp PYTHONPATH=. ENCRYPTION_KEY=<supplied> TEST_DATABASE_URL=<assigned> /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -o addopts='--strict-markers' -q -m integration tests/integration/test_backup_restore_roundtrip.py`
  - `1 passed, 1 warning in 1.25s`
- Integration run 2, same command:
  - `1 passed, 1 warning in 1.32s`
- Integration run 3, same command:
  - `1 passed, 1 warning in 1.24s`

### Round 1 open points

- The integration runs still emit Alembic's existing `No path_separator found in configuration`
  deprecation warning. Changing `alembic.ini` is outside OPS-5's allowed files.
- No command touched the live stack or any database other than `qradar_obs_ops5` and scratch
  databases derived by the integration test. No commit was made.
