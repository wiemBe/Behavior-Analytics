# Task OPS-7b — `./qradar-dash doctor`: the host half

Read `AGENTS.md` at the repository root first and follow it exactly. §5 (SELinux) and §2 rule 5
(no secrets) matter most. Then read:
- `docs/OPERATIONS-DESIGN.md` §6 (binding: the line format, the exit codes, the `disk` row);
- §3.2's SELinux and Clock rows (around lines 444–445);
- §5.3 (the backup disk guard);
- §8 for the runbook ids `OPS-R<n>`.

Then read the operator script `qradar-dash` (OPS-4) and its tests in
`backend/tests/unit/test_qradar_dash_installer.py`. OPS-7 has already merged the in-container
half: `backend/app/cli/doctor.py`, run as `python -m app.cli.doctor`. Its module docstring says
the host prints `qradar_clock` and disk free space.

## Build

Replace the `doctor) not_built …` stub in `qradar-dash` with `cmd_doctor`.

1. **`need_installed`** first, as `status` does. Docker missing is a FAIL line, not a crash.

2. **Host checks.** Each prints one line in the in-container format (design §6):
   `STATUS  id  message    runbook: OPS-Rn`, with the runbook only on WARN and FAIL. Use these
   ids:
   - **`disk_docker`:** `df -P` of Docker's root directory
     (`docker info --format '{{.DockerRootDir}}'`).
     - PASS under 70 % used;
     - WARN from 70 % up to 90 %;
     - FAIL at 90 % or more.
     - Print the used percent and the free space in GiB.
   - **`disk_backup`:** the same thresholds on the backup directory the installer recorded in
     `.env` (find its key with `env_get`). Then the §5.3 guard: FAIL when free space is less than
     `2 × database size + 2 GiB`.
     - Read the database size with `psql` in the postgres container, the way OPS-4's
       `admin_exists` reads a count: `SELECT pg_database_size(current_database())`.
     - A backup directory that does not exist yet is WARN, naming the path. OPS-5 builds
       `backup`.
     - Postgres not running means the guard part is WARN "database size unavailable", not
       FAIL.
   - **`qradar_clock`:** run `timedatectl show -p NTPSynchronized --value`.
     - PASS on `yes`;
     - WARN on anything else, with runbook OPS-R15;
     - WARN with "timedatectl is not available; check NTP by hand" when the command is missing.
   - **`selinux`:** print `getenforce`'s output as PASS. `Enforcing` is normal.
     - A host without `getenforce` is PASS "SELinux is not installed".
     - **Never** call `setenforce`, `chcon`, `restorecon` or `semanage`.
   - **`containers`:** read `docker compose … ps --all --format json` through the script's `dc`
     helper.
     - PASS when every service in it is running and, where it has a healthcheck, healthy.
     - FAIL naming each service that is not, with its state.
     - Pick the runbook id from §8 that matches a stopped or unhealthy container. If none
       matches, print no runbook rather than a wrong one.
     - `qradar-mcp` in a stopped state is not a failure when the `mcp` profile is off.

3. **The in-container half.** Then run `dc exec -T backend python -m app.cli.doctor` and print its
   output unchanged.
   - Keep its exit code.
   - When the backend container is not running, print one FAIL line `doctor_incontainer` saying
     so, with the runbook for a down service, and do not run `exec`.

4. **Alignment and exit code.**
   - Align the id column across both halves where cheap. Do not re-parse the in-container lines
     to do it: printing the host lines with the same width rule is enough.
   - Exit with the worst status over both halves: 0 when all PASS, 1 on any WARN with no FAIL,
     2 on any FAIL.

5. **Clean-ups.**
   - Remove the "(OPS-7)" note in `summary()`.
   - Keep `usage` accurate.
   - Read-only throughout: the doctor writes no file, changes no setting and starts or stops no
     container.
   - It prints no `.env` value except the backup path and the project name.

## Tests

New file: `backend/tests/unit/test_qradar_dash_doctor.py`.
- Use the same approach as the installer tests: copy the script into `tmp_path`, write a fixture
  `.env`, and put **stub executables** on a PATH of your own for `docker`, `df`, `timedatectl`
  and `getenforce`. Each stub prints fixture output and exits with a fixture code.
- No real Docker, and no network.
- Cover:
  - all PASS gives exit 0;
  - Docker root at 75 % gives WARN and exit 1;
  - Docker root at 92 % gives FAIL and exit 2;
  - the backup guard failing (free < 2 × size + 2 GiB) gives FAIL;
  - a missing backup directory gives WARN;
  - NTP not synchronized gives WARN;
  - no `timedatectl` gives WARN;
  - no `getenforce` gives PASS;
  - an unhealthy service gives FAIL naming it;
  - a stopped `qradar-mcp` with the profile off is not a failure;
  - in-container exit 2 with host all PASS gives exit 2;
  - the backend not running gives the `doctor_incontainer` FAIL and no `exec` call (the stub
    records its arguments);
  - a planted value in `.env` (for example `POSTGRES_PASSWORD=planted-secret`) never appears in
    the output.
- Break each guard once, confirm a test fails, then restore it.

## Gates

- `shellcheck qradar-dash`, clean. If shellcheck is not on PATH, say so; do not install system
  packages.
- From `backend/` with `PYTHONPATH=.` and the main checkout's venv:
  - `ruff check app tests`, clean;
  - the new test file;
  - `tests/unit/test_qradar_dash_installer.py`, still green.
- No integration run is needed. No Docker container is started by this task.

## Allowed files

- `qradar-dash`
- `backend/tests/unit/test_qradar_dash_doctor.py` (new)
- `docs/OPERATIONS-DESIGN.md`: §6 only. Mark the host half as built, with one as-built sentence
  per check id that differs from the plan.
- `docs/tasks/OPS-7b.report.md` (new)

Do not commit. Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops7b/`, never `/tmp`.
