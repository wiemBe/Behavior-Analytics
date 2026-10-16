# OPS-7b report

## Files changed

- `qradar-dash` — replaced the `doctor` stub with read-only host checks, Compose health handling,
  in-container doctor execution, aligned output, and worst-status exit propagation; updated usage
  and removed the OPS-7 summary note.
- `backend/tests/unit/test_qradar_dash_doctor.py` — added stub-only coverage for host disks, the
  backup guard, NTP, SELinux, Compose services and profiles, backend availability, exit codes, and
  secret non-disclosure.
- `docs/OPERATIONS-DESIGN.md` — marked the §6 host half built and documented the as-built host and
  wrapper check ids.
- `docs/tasks/OPS-7b.report.md` — added this report.

## What I did

- Added `disk_docker` and `disk_backup` checks with 70%/90% thresholds, GiB free-space output, and
  the §5.3 `2 × database size + 2 GiB` backup guard using a read-only `psql` query.
- Added `qradar_clock`, `selinux`, and `containers` checks, including the profile-off exception for
  stopped `qradar-mcp` and conservative runbook selection.
- Added backend-running detection and unchanged streaming of `python -m app.cli.doctor` output;
  host and container results combine to exit 0, 1, or 2 by worst status.
- Kept the command read-only: it writes no file, changes no setting, and starts or stops no
  container. Tests use fixture executables only and make no network or Docker-daemon call.
- Mutation verification: a broad deliberate guard inversion produced
  `FFFFFFFFFF.FFF..F.F                                                      [100%]` with 15
  failures; a second targeted mutation produced
  `FFFFFF                                                                   [100%]` with 6
  failures. The correct implementation was restored, and the final focused run is green.

## Gates

- `shellcheck qradar-dash` — unavailable, exit 127. Verbatim result:
  `bash: shellcheck: command not found`
- `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/ruff check app tests` — exit 0.
  Verbatim result: `All checks passed!`
- `TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops7b/pytest-tmp PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -q tests/unit/test_qradar_dash_doctor.py`
  — exit 0. Verbatim result:
  `...................                                                      [100%]`
- `TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops7b/pytest-tmp PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -q tests/unit/test_qradar_dash_installer.py`
  — exit 1. Verbatim result:
  `........................F..                                              [100%]`
  and
  `FAILED tests/unit/test_qradar_dash_installer.py::test_unbuilt_subcommands_exit_non_zero`.
  The failure is the pre-existing assertion that `doctor` is still unbuilt and exits 64; OPS-7b
  requires `need_installed` first, which correctly exits 1 for that test's copy without `.env`.
- `bash -n qradar-dash` — exit 0, no output.
- `git diff --check` — exit 0, no output.
- Integration tests — not run; the task says none are needed and supplied no database.
- Full non-integration suite — not run; OPS-7b specifies the new doctor file and the installer
  file as its pytest gates.

## Open points

- `backend/tests/unit/test_qradar_dash_installer.py` is outside the allowed files but still lists
  `doctor` among unbuilt commands. The orchestrator must remove `doctor` from that stale assertion
  (or otherwise update its expected installed-state behavior) for the installer gate to be green.
- No other open points. No commit was created.

## Round 1

### Changes

- Removed only `"doctor"` from the installer test's tuple of unbuilt subcommands, resolving the
  stale gate assertion authorized in review.
- Replaced the Compose JSON request and `awk` parser with the reviewed four-field Go template.
  The Docker stub verifies the exact template argument before returning tab-separated fixture rows.
- Added one-shot `migrate` handling: exit 0 is ignored and not counted; a non-zero exit is a
  `containers` FAIL that names the exit code and links OPS-R11.
- Simplified container runbook selection by collecting one mapping per failure and emitting a
  runbook only when all mappings are the same non-empty id. Tests cover unanimous and mixed
  mappings.
- Updated the §6 as-built sentence for the successful `migrate` exception.
- Broke the successful-migrate exemption by changing its accepted exit code from 0 to 99. The
  focused mutation run failed as required with the verbatim result
  `F                                                                        [100%]` and
  `FAILED tests/unit/test_qradar_dash_doctor.py::test_successful_exited_migrate_is_ignored_and_not_counted`.
  The exit-0 rule was then restored before the final gates.

### Gates

- `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/ruff check app tests` — exit 0.
  Verbatim result: `All checks passed!`
- `TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops7b/pytest-tmp PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -q tests/unit/test_qradar_dash_doctor.py`
  — exit 0. Verbatim result:
  `.......................                                                  [100%]`
- `TMPDIR=/home/efe/Documents/QRadar-Dash-work/ops7b/pytest-tmp PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -q tests/unit/test_qradar_dash_installer.py`
  — exit 0. Verbatim result:
  `...........................                                              [100%]`
- `bash -n qradar-dash` — exit 0, no output.

### Open points

- None. The prior stale installer assertion is resolved. No commit was created.
