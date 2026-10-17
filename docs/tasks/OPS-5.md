# Task OPS-5 — `./qradar-dash backup` and `restore`: verified backups, a disk guard, backup retention, the daily timer

**This task can destroy a database. Read every line.**

Read `AGENTS.md` at the repository root first and follow it exactly. §5 (SELinux) and §2 rule 5
(secrets) matter most. Then read `docs/OPERATIONS-DESIGN.md`:
- **§5.1 to §5.4 (binding):** what a backup is, the dump and restore sequence, the disk guard
  and retention, and the restore half of §5.4;
- §4.5 (the state file);
- §6's `disk_backup` (`doctor` already implements the guard check);
- §10.2's OPS-5 line.

Then read the operator script `qradar-dash`:
- `dc`, `env_get`, `need_installed`;
- the `admin_exists` / `doctor_database_size` read-only `psql` pattern;
- `cmd_doctor`'s `disk_backup` (reuse its guard arithmetic: do not write it twice);
- `cmd_support_bundle` (the staging, trap and fail-closed pattern);
- `deploy/load-secrets.sh` (the stdin-tar approach to the secrets volumes).

Then read its tests: `backend/tests/unit/test_qradar_dash_*.py` (stub executables on PATH).

## Decisions already taken by the orchestrator

1. **Scope.** This task covers `backup`, `restore <path>` and the systemd timer.
   - `rollback` and `upgrade` are OPS-6.
   - Put the restore sequence in **one** shell function that OPS-6's `rollback` will call. Its
     arguments are the backup directory and a reason.
2. **A backup never touches the live database, except to read it.**
   - The dump runs inside the postgres container: `pg_dump -Fc` to a path under that
     container's `/tmp`.
   - It is copied out with `docker compose cp` (or streamed with `exec -T … cat`) into the new
     backup directory, then removed inside the container.
   - Scheduled and manual backups run with the writers up (design §5.2).
3. **The backup directory.**
   - It is `qradar-dash-<UTC yyyymmddThhmmssZ>-<reason>/`, mode 0700, under the backup root
     from `.env`. Every file inside is mode 0600.
   - Contents per §5.1:
     - `database.dump`;
     - `env.bak`;
     - `secrets.tar`: the QRadar secrets volume and the proxy certs volume, read through a
       short-lived container that mounts the **named volumes** read-only. A named volume needs
       no SELinux label. Never bind-mount the checkout or `.secrets/`; never `:z`. Tar to
       stdout.
     - `state.json` if `.qradar-dash/state.json` exists;
     - `MANIFEST`: the version, the image digests, `alembic_version`, the database size in
       bytes and the hostname.
   - The directory is written as `.<name>.partial` and renamed to its final name only after
     the verify passes. **A failure at any step removes the partial directory and exits
     non-zero**, through a trap.
4. **Disk guard (§5.3).**
   - Refuse **before writing anything** when free space on the backup filesystem is less than
     `2 × pg_database_size + 2 GiB`.
   - Print the sizes and the path.
   - Use the same arithmetic as `doctor`'s `disk_backup`; factor it into one helper.
5. **Verify (§5.3).**
   - Unless `--no-verify` is given, restore the dump into a scratch database
     `qradar_obs_restore_test` with the §5.2 sequence:
     1. `CREATE DATABASE`;
     2. `CREATE EXTENSION IF NOT EXISTS timescaledb` and `pgcrypto`;
     3. `timescaledb_pre_restore()`;
     4. `pg_restore --no-owner`;
     5. `timescaledb_post_restore()`.
   - Check that `alembic_version` matches the source and that `SELECT count(*)` on
     `alembic_version` and `role` succeeds.
   - Then drop the scratch database, **on every path**, success or failure.
   - Every statement that drops, creates or renames a database names it **literally**: either
     `qradar_obs_restore_test`, or a name the script built from a fixed prefix and a UTC
     stamp.
   - **No code path may drop or rename the database whose name is `POSTGRES_DB`, except the
     final swap in `restore` (item 8).** Add a guard function that refuses to drop a name equal
     to `POSTGRES_DB`, call it before every `DROP DATABASE`, and test it.
6. **Retention (§5.3)**, applied only after a successful backup:
   - keep the last 7 `manual` or `scheduled` backups;
   - keep every `pre-upgrade` backup until two newer upgrades have `outcome=success` in the
     state file;
   - never delete the backup recorded in `last_upgrade.backup` while that outcome is
     `failed` or `rolled_back`;
   - delete a backup only when the guard for the next backup still passes without it;
   - delete only directories matching the exact `qradar-dash-<stamp>-<reason>` pattern
     directly inside the backup root; never anything else, never by glob outside it;
   - a missing or unreadable state file means `pre-upgrade` backups are kept (fail closed).
7. **`restore <path>` (§5.4, last paragraph).**
   - **Confirmation:** ask for the word `restore` on stdin. With `--non-interactive`, require
     `--yes`.
   - **Refuse, before stopping anything,** when any of these holds:
     - the path is not a complete backup directory (all five files, the right modes);
     - its `MANIFEST` version is newer than the images it would start;
     - its `env.bak` `ENCRYPTION_KEY` is neither the current `.env` `ENCRYPTION_KEY` nor one
       of the keys in `ENCRYPTION_KEYS_JSON`. The restored ciphertext would be unreadable.
       Print what to do: put the backup's key back as described in OPERATIONS-DESIGN §5.1,
       without printing any key.
   - **The guard** must pass for one more database of the backup's size (the restored copy
     sits beside the live one).
8. **The restore sequence**, which `rollback` will call:
   1. Stop the writers and the UI: backend, celery-worker, celery-beat, frontend, proxy if
      present. Postgres and Redis stay up.
   2. Restore into `qradar_obs_restore_<UTC stamp>` with the §5.2 sequence, and verify
      `alembic_version` against the `MANIFEST`.
   3. Terminate the other connections to `POSTGRES_DB`.
   4. `ALTER DATABASE <POSTGRES_DB> RENAME TO qradar_obs_broken_<UTC stamp>`.
   5. `ALTER DATABASE qradar_obs_restore_<stamp> RENAME TO <POSTGRES_DB>`.
   6. Start the stopped services again, and wait for `/api/v1/health/ready`.
   7. Print the name of the kept `qradar_obs_broken_<stamp>` copy, and the exact `psql` command
      the operator can run to drop it later. **Never drop it automatically.**
   - If a step before 4 fails: drop the restore database (through the guard), restart the
     services, and exit non-zero. The live database is untouched.
   - If step 5 fails after step 4 succeeded: rename the broken copy back to `POSTGRES_DB` and
     restart.
   - Secrets are **not** restored automatically. Print that `secrets.tar` is in the backup,
     and give the command to inspect it.
9. **The timer.**
   - `./qradar-dash backup --install-timer` writes `qradar-dash-backup.service` and
     `qradar-dash-backup.timer` (daily, `Persistent=true`, `RandomizedDelaySec=15min`), with
     `ExecStart` set to this checkout's absolute `qradar-dash backup` path and reason
     `scheduled`.
   - The units are written under `/etc/systemd/system` when run as root, else under
     `~/.config/systemd/user`. Print which.
   - Then it enables them.
   - The script never installs the timer on its own, and CI never enables it.
   - `--print-timer` prints both unit files without writing anything; the tests use that.
10. **Never:**
    - `docker volume rm`, `docker system prune` or any prune;
    - `alembic downgrade`;
    - deleting outside the backup root;
    - printing a key, a password or a token;
    - `:z`, or a bind mount of the checkout.

## Tests

### 1. Shell tests (new) — `backend/tests/unit/test_qradar_dash_backup.py`

Use the stub-executable harness. No real Docker.

The `docker` stub serves:
- `compose exec`, `cp`, `run`, `stop`, `start`, `ps`;
- `psql` answers for size, `alembic_version` and counts.

It records every argument line and every SQL statement it receives on stdin.

Cover:
- **The disk guard:**
  - it refuses before any directory is created and before any `pg_dump`;
  - it passes at exactly the threshold.
- **A successful backup:**
  - it produces the final directory with mode 0700, five files with mode 0600, and a
    MANIFEST with the five fields;
  - no `.partial` directory is left.
- **A failure at any of these steps** removes the partial directory, exits non-zero, and drops
  the scratch DB if it was created:
  - the dump;
  - the copy-out;
  - the secrets tar;
  - the verify (alembic mismatch).
- **`--no-verify`** skips the scratch DB.
- **Retention:**
  - Fixture: eight `manual` or `scheduled` directories, two `pre-upgrade` directories, a state
    file, and one unrelated directory and one unrelated file in the backup root.
  - Assert exactly which directories remain.
  - The unrelated entries are untouched.
  - A missing state file keeps every `pre-upgrade` directory.
  - The `failed`/`rolled_back` rule holds.
- **The `POSTGRES_DB` guard:** across every test, the recorded SQL never contains
  `DROP DATABASE <POSTGRES_DB>`. Write one test that calls the guard function directly with
  `POSTGRES_DB` and expects a refusal.
- **`restore`:**
  - It refuses (with no `stop` call recorded) when:
    - the confirmation word is wrong;
    - `--yes` is missing under `--non-interactive`;
    - a file is missing;
    - the MANIFEST version is newer;
    - the `ENCRYPTION_KEY` does not match.
    - In each case, check that no key appears in the output.
  - On success, the recorded order is: stop the writers, create and restore the restore DB,
    verify, terminate connections, rename live to `broken_<stamp>`, rename restore to live,
    start, and **no** drop of the broken copy.
  - On a failure at the verify step: the restore DB is dropped, no rename happens, and the
    services are restarted.
  - On a failure at step 5: the broken copy is renamed back.
- **`--print-timer`:** prints a service and a timer with `OnCalendar=daily`,
  `Persistent=true` and the absolute `ExecStart`, and writes nothing.

### 2. Integration test (new) — `backend/tests/integration/test_backup_restore_roundtrip.py`

Run the §5.2 sequence for real, against the suite's PostgreSQL + TimescaleDB, with
`TEST_DATABASE_URL`.

1. Migrate a fresh scratch database to head with Alembic.
2. Insert a known row.
3. Create a hypertable row in `log_source_metric`.
4. `pg_dump -Fc`, run inside the test-db container or with the host's `pg_dump` if present.
   Choose whichever the test can do without a bind mount, and skip with a clear reason only
   when neither tool is available.
5. Restore into a second scratch DB with `timescaledb_pre_restore`/`post_restore`.
6. Assert:
   - `alembic_version` is equal;
   - the known row survives;
   - the hypertable row survives;
   - the hypertable is still a hypertable.
7. Drop both scratch databases, also on failure.

Name the scratch databases from the test DB's own name plus a suffix. Never hard-code a DB
name (AGENTS.md §4).

- Break each guard once, confirm a test fails, then restore it: the disk guard, the
  `POSTGRES_DB` drop guard, the partial-directory cleanup, the encryption-key check, and the
  step-5 rename-back.

## Gates

- shellcheck, from the image, reading stdin:
  `docker run --rm -i koalaman/shellcheck:stable - < qradar-dash`;
- from `backend/`, with `PYTHONPATH=.` and the main checkout's venv:
  - `ruff check app tests`;
  - all `tests/unit/test_qradar_dash_*.py`;
  - the unit suite;
  - the new integration test on `postgresql+asyncpg://test:test@localhost:5433/qradar_obs_ops5`.
    It is your DB. Run it three times.
- **The installer test's stub tuple:** remove `"backup"` and `"restore"` from
  `test_unbuilt_subcommands_exit_non_zero`, and change nothing else in that file.

## Allowed files

- `qradar-dash`
- the two new test files
- `backend/tests/unit/test_qradar_dash_installer.py` (that one tuple only)
- `docs/OPERATIONS-DESIGN.md` §5, for as-built notes, including the encryption-key refusal
- `docs/tasks/OPS-5.report.md` (new)

Do not commit. Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops5/`, never `/tmp`
(set `TMPDIR` for pytest).

**No command in this task touches the live stack or any database except your own test DB and
the scratch databases your integration test names.**
