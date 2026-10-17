# Task OPS-6 — `./qradar-dash upgrade <version>` and `rollback`, the state file, automatic rollback

**This task can destroy a database and leave production on the wrong images. Read every line.**

Read `AGENTS.md` at the repository root first and follow it exactly. §5 (SELinux) and §2 rule 5
(secrets) matter most. Then read `docs/OPERATIONS-DESIGN.md`:
- **§4.1 to §4.5 (binding):** preflight, backup then migration, the Redis flush, automatic
  rollback, the state file;
- **§5.4 (binding):** `rollback`;
- §1.1 and §1.3: version rules and the release notes' `min_from`;
- §5.1 to §5.3 and the as-built notes OPS-5 added there;
- §6: the `doctor` ids `db_connect` and `migration_head`.

Then read the operator script `qradar-dash` in full, especially what OPS-5 built: the backup
function, **the one shared restore function** (it takes a backup directory and a reason) and its
`POSTGRES_DB` drop guard, the disk-guard helper, and how the retention code reads
`.qradar-dash/state.json`. Read `docs/tasks/OPS-5.report.md`. Then read the tests
`backend/tests/unit/test_qradar_dash_*.py` (the stub-executable harness).

## Decisions already taken by the orchestrator

1. **Scope.** `upgrade <version>`, `rollback`, writing the state file (install, upgrade,
   rollback, restore), and the `status` lines of §5.5 that read it. The real-stack rehearsal
   that measures downtime is a separate task (OPS-6r); this task uses stubs only.
2. **The state file** (§4.5): `.qradar-dash/state.json`, file mode 0600, directory 0700.
   - Written by **one** function, atomically: a temporary file in the same directory, then
     `mv`. A failed write leaves the old file intact and exits non-zero.
   - Every value written is validated first: versions `^[0-9]+\.[0-9]+\.[0-9]+$`, digests
     `^sha256:[0-9a-f]{64}$`, UTC times `^[0-9]{4}-…Z$`, the backup path an absolute path with
     no quote, backslash or control character. The writer refuses anything else, so no
     escaping is needed and nothing from `.env` can reach the file.
   - **Read it with the same method OPS-5's retention uses.** A file that does not parse, or
     lacks a field `upgrade` or `rollback` needs, makes them refuse with the path and the
     field (fail closed). Never guess a missing field.
   - `install` writes it at the end of a successful run: `installed_version` and `images`
     (the digests of the running containers), `previous_images: null`, no `last_upgrade`.
     An idempotent re-run keeps an existing `previous_images` and `last_upgrade`.
   - A successful `restore <path>` writes `installed_version` from the backup's `MANIFEST`
     and clears an `outcome=failed` block (§4.1, last bullet), recording
     `last_upgrade.outcome` as it was plus `restored_from` with the path.
3. **`upgrade <version>`: the preflight (§4.1), in this order, before any container stops.**
   - Refuse `latest`, a leading `v`, a hyphen, or anything that is not plain SemVer.
   - Read the state file. The same version with healthy containers exits 0 and changes
     nothing. A newer installed version is refused ("that is `rollback`"). A recorded
     `outcome=failed` refuses until `rollback` or `restore` clears it.
   - **Pull the target images first** (or find them loaded, for `--offline`). A pull failure
     exits non-zero with no container stopped.
   - Read `/app/releases/<version>.md` from the **target backend image**
     (`docker run --rm --entrypoint cat …`). Its front matter must parse and its `version`
     must equal the target; `installed_version` must be at least `min_from`. Otherwise stop,
     images pulled, containers untouched.
   - The disk guard (OPS-5's helper) passes for a dump plus a scratch restore.
   - `doctor`'s `db_connect` and `migration_head` are not already FAIL (§4.1; point at
     symptom 11).
4. **The sequence (§4.2).**
   1. Record the running digests as `previous_images`, and the current
      `QRADAR_DASH_VERSION` value.
   2. Stop backend, celery-worker, celery-beat, frontend and proxy (if present). Postgres and
      Redis stay up.
   3. Run OPS-5's backup with reason `pre-upgrade`, **always verified**. A failed backup
      aborts: start the stopped services on the **old** images, exit non-zero, state
      unchanged.
   4. `FLUSHALL` inside the redis container (§4.2's paragraph).
   5. Set `QRADAR_DASH_VERSION=<target>` in `.env` atomically (the same temp-and-`mv` way),
      changing that one line only. The backup's `env.bak` already holds the old file.
   6. `run --rm migrate` on the new backend image.
   7. On exit 0, `up -d` the stopped services.
   8. Wait up to 180 s: `/api/v1/health/ready` is 200 with `"database": "ok"`;
      `/api/v1/version` reports the target; `doctor`'s `db_connect` and `migration_head` PASS.
   9. Record `last_upgrade` with `outcome=success`, the backup path, `from`, `to` and `at`;
      `installed_version` and `images` become the new ones.
5. **Automatic rollback (§4.3).** Triggered **only** by: migrate non-zero; a required service
   unhealthy at 180 s; the wrong version from `/api/v1/version`; `db_connect` or
   `migration_head` FAIL. **Never** by a QRadar check, LDAP, a late search family or a disk
   WARN. The action is the `rollback` sequence below with the backup just taken. If the
   rollback's own health check fails: stop the writers again, print the backup path, record
   `outcome=failed`, exit non-zero. **No third strategy.**
6. **`rollback` (§5.4).** No argument.
   - Refuse, before stopping anything, when `previous_images` is null, the
     `last_upgrade.backup` directory is missing or incomplete, or a previous image is not
     present locally **with the recorded digest** (it never pulls).
   - Restore with **OPS-5's shared restore function** (do not write a second one). It keeps
     the broken copy and never drops it.
   - Put the previous `QRADAR_DASH_VERSION` back in `.env` (atomically), start the services on
     the previous images, and run the §4.2 step 7 checks expecting the **previous** version.
   - Record `outcome=rolled_back`. Never `alembic downgrade`.
7. **Never:** `docker volume rm`, any prune, `alembic downgrade`, deleting any backup, printing
   a key, password or token, `:z`, a bind mount of the checkout, or a pull inside `rollback`.

## Tests — `backend/tests/unit/test_qradar_dash_upgrade.py` (new)

Use the stub-executable harness; no real Docker. The `docker` stub serves `compose pull`,
`stop`, `run --rm migrate`, `up -d`, `exec redis redis-cli FLUSHALL`, `image inspect` (digests)
and `run --entrypoint cat` (release notes), and records every call in order. `curl` is stubbed
for the health and version endpoints. Stub OPS-5's backup and restore at the `docker`/`psql`
level the way OPS-5's tests do, so the real shell functions run.

Cover:
- **Preflight refusals, each with no `stop` recorded:** `latest`, `v0.2.0`, `0.2.0-rc1`; a
  newer installed version; a recorded `failed` outcome; a pull failure; release notes missing,
  unparsable, with the wrong `version`, or with `min_from` above the installed version; the
  disk guard; `migration_head` already FAIL; a state file that does not parse.
- **The same version, healthy:** exit 0 and no `stop`, `pull` of a new tag or state write.
- **Success:** the recorded order is pull, notes, stop, backup (pre-upgrade, verified),
  `FLUSHALL`, `.env` edit, migrate, `up -d`, health, version, doctor; the state file has the
  new version and digests, `previous_images` the old ones, `outcome=success`, mode 0600; `.env`
  differs from before in the `QRADAR_DASH_VERSION` line only.
- **A failed backup:** services restarted on the old images, no migrate, state unchanged.
- **Automatic rollback** on each trigger (migrate non-zero; health timeout, with the clock
  stubbed so the test does not wait 180 s; wrong version; `migration_head` FAIL): the restore
  function runs, `.env` has the old version again, the old images start, `outcome=rolled_back`.
- **Not a trigger:** a `doctor` QRadar FAIL after a healthy start leaves `outcome=success`.
- **The rollback's own health fails:** writers stopped, the backup path printed,
  `outcome=failed`, exit non-zero, and no third attempt recorded.
- **`rollback` refusals** (null `previous_images`, a missing backup, a local digest mismatch),
  each with no `stop` and no `pull` recorded.
- **The state writer** refuses an invalid version, digest or path, and a failed write leaves
  the old file byte-for-byte.
- No test output contains the planted `ENCRYPTION_KEY`, Postgres password or token.

Break each guard once, confirm a test fails, then restore it: the "not a trigger" rule, the
pull-before-stop order, the local-digest check, the `failed` block, and the atomic state write.

## Gates

- shellcheck from the image on stdin: `docker run --rm -i koalaman/shellcheck:stable - < qradar-dash`;
- from `backend/`, with `PYTHONPATH=.` and the main checkout's venv: `ruff check app tests`,
  every `tests/unit/test_qradar_dash_*.py`, and the unit suite;
- **the installer test's stub tuple:** remove `"upgrade"` and `"rollback"` from
  `test_unbuilt_subcommands_exit_non_zero` and change nothing else in that file.

## Allowed files

- `qradar-dash`
- `backend/tests/unit/test_qradar_dash_upgrade.py` (new)
- `backend/tests/unit/test_qradar_dash_installer.py` (that one tuple only, plus new tests for
  the state file written by `install`)
- `docs/OPERATIONS-DESIGN.md` §4 and §5.4, for as-built notes
- `docs/tasks/OPS-6.report.md` (new)

Do not commit. Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops6/`, never `/tmp`.
**No command in this task touches a running stack, a real image or any database.**
