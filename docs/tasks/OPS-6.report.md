# OPS-6 report

## What I inherited and what I changed in it

Codex left (uncommitted, no report) about 660 lines in `qradar-dash` (state reader and writer,
`cmd_upgrade`, `cmd_rollback`, `perform_rollback`, status lines, install and restore state),
`backend/tests/unit/test_qradar_dash_upgrade.py` (~840 lines), a state test pair in
`test_qradar_dash_installer.py`, and as-built notes. It was largely correct and complete: the
preflight order (pull and notes before any stop), the subshell backup, FLUSHALL, atomic `.env`
and state writes with validated values, the trigger list, the local-digest check and no pull in
`rollback` were all present, and all 136 inherited tests and shellcheck passed. Defects I found
and fixed:

1. No restart on interrupt or `die` between the stop and the `.env` edit; only explicit calls.
   Added `upgrade_restart_old` plus EXIT/HUP/INT/TERM traps, cleared at the point of no return.
2. `at` (UTC time) was read after `.env` was already changed; a failure there left the new
   version in `.env` with services stopped. Moved before the stop.
3. The old version came from the state file only. Now `.env`'s `QRADAR_DASH_VERSION` must equal
   `installed_version`, else refuse before any stop.
4. The doctor preflight only refused on an explicit FAIL; a doctor that printed nothing passed.
   Now both `db_connect` and `migration_head` must be reported PASS (fail closed).
5. An automatic rollback that succeeded made `upgrade` exit 0. It now exits non-zero ("rolled
   back"); decision for the orchestrator to confirm, the spec is silent.
6. `record_rollback_failure` rewrote `.env` to the old version even when the swap had failed
   (DB still new). Removed; it now only stops the writers, prints the backup path and the
   manual commands, and records `failed`. Wording no longer says "Automatic" for manual rollback.
7. `rollback` did not check the backup's `ENCRYPTION_KEY` (restore does). Added, before any stop.
8. A direct `restore` with no `last_upgrade` invented one. It no longer does.
9. `status` died on a missing or invalid state file. It now names the field, prints
   `Installed version: unknown` and exits 1 after the compose output.
10. Tests: stronger assertions (rollback exit code, single swap, no swap on the non-trigger, state
    after rollback, success backup fields) and 8 new tests (silent doctor, env/state mismatch,
    Redis-flush and `.env`-write failure restarting old services, rollback state missing a
    field, rollback key refusal, status with invalid state, restore without history).

Not changed: the 180 s `up --wait` and the readiness loop are two separate 180 s waits (worst
case about 360 s before an automatic rollback); the spec says "up to 180 s".

## Files changed

- `qradar-dash`
- `backend/tests/unit/test_qradar_dash_upgrade.py` (new)
- `backend/tests/unit/test_qradar_dash_installer.py` (the tuple, plus the two install-state tests)
- `docs/OPERATIONS-DESIGN.md` (as-built notes in section 4.5 and 5.4)
- `docs/tasks/OPS-6.report.md`

## Gates (from /home/efe/Documents/QRadar-Dash-wt/ops6, backend/ for Python)

- `docker run --rm -i koalaman/shellcheck:stable - < qradar-dash`: no output, exit 0
- `bash -n qradar-dash && git diff --check`: no output, exit 0
- `ruff check app tests`: `All checks passed!`
- `mypy app`: `Success: no issues found in 244 source files`
- `pytest -o addopts='--strict-markers' -q tests/unit/test_qradar_dash_*.py`: `144 passed in 14.16s`
- `pytest -o addopts='--strict-markers' -q -m 'not integration'`:
  `3647 passed, 10 skipped, 1303 deselected, 5 warnings in 82.05s (0:01:22)`

## Guards broken once (each restored; file compared identical afterwards)

- Not-a-trigger rule (any doctor FAIL fails the check): `test_qradar_doctor_failure_is_not_a_rollback_trigger`
- Pull before stop (stop inserted before the pull): 21 failures, incl. `test_pull_failure_is_before_stop`
- Local-digest check (always true): `test_rollback_refuses_digest_mismatch_without_stop_or_pull`
- `failed` block (never matches): `test_failed_outcome_blocks_upgrade_before_pull_or_stop`
- Atomic state write (mv replaced by cat into the file): `test_failed_atomic_state_move_keeps_old_file_byte_for_byte`
  and `test_success_has_safe_order_private_state_and_one_line_env_edit`
- Backup in a subshell (called directly): `test_failed_backup_restarts_old_services_and_keeps_state`

## Open points

- Exit code of a successfully rolled-back upgrade is non-zero (item 5); confirm.
- Failed rollback leaves `.env` as it was when the swap failed; state records the old version
  and old images as both `images` and `previous_images`, so `rollback` can be retried.
- The install-state tests call `write_install_state` directly, not a full `install` run.
- No integration tests, no real Docker beyond shellcheck, no commit.

## Round 2

Confirmed by the orchestrator: a successful automatic rollback makes `upgrade` exit non-zero.
Noted: `up --wait --wait-timeout 180` and the readiness loop are two separate 180 s waits
(worst case about 360 s before an automatic rollback starts).

1. **Crash-safety.** `upgrade` now writes the state before the `.env` edit and the migrate:
   `installed_version` = old, `images` = `previous_images` = old images, `last_upgrade` = {at,
   from old, to target, `outcome=failed`, pre-upgrade backup}. Success, `rolled_back` and a failed
   rollback overwrite it. If that write fails, the old services are restarted and `upgrade` exits
   non-zero before the `.env` edit. If the `.env` edit itself fails, the earlier state is written
   back and the old services restart. Tests: the recorded order now shows `state-write` before
   `env-edit` and migrate (and two writes in total);
   `test_crash_during_migrate_leaves_state_that_lets_rollback_proceed` (the migrate stub
   SIGKILLs its parent shell) shows the state, a blocked `upgrade`, `rollback` passing every
   refusal up to the confirmation, and a confirmed rollback finishing with `rolled_back` and the
   old `.env`; `test_attempt_record_failure_restarts_old_services_before_env_edit`.
2. **Manual `rollback` asks first.** After all refusals and before any stop it reads the word
   `rollback` from stdin; `--non-interactive` requires `--yes`; the automatic rollback does not
   ask. `usage`, the header comment and the as-built notes (section 4.5, 5.4) are updated.
   Tests: wrong word, missing `--yes`, no stdin (each: no stop recorded), the right word
   proceeds. The other rollback tests now pass `--non-interactive --yes`.

### Gates (verbatim result lines)

- `docker run --rm -i koalaman/shellcheck:stable - < qradar-dash`: no output, `sc=0`
- `bash -n qradar-dash; git diff --check`: no output
- `ruff check app tests`: `All checks passed!`
- `mypy app`: `Success: no issues found in 244 source files`
- `pytest -o addopts='--strict-markers' -q tests/unit/test_qradar_dash_*.py`: `150 passed in 17.31s`
- `pytest -o addopts='--strict-markers' -q -m 'not integration'`:
  `3653 passed, 10 skipped, 1303 deselected, 5 warnings in 77.72s (0:01:17)`

### Guard broken

The attempt-state write moved to after the migrate (just before the start): three tests failed,
`test_crash_during_migrate_leaves_state_that_lets_rollback_proceed`,
`test_success_has_safe_order_private_state_and_one_line_env_edit` and
`test_attempt_record_failure_restarts_old_services_before_env_edit`. `qradar-dash` restored and
compared byte-identical to the saved copy.
