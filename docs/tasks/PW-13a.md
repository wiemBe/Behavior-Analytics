# Task PW-13a — History bootstrap, foundations: migration `0029`, models, enums, settings

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 6–8, §3, and §4
(tests, break-it, the whole integration run). Then read the design
`docs/PHASE-G-HISTORY-BOOTSTRAP.md`. It is binding. Read all of it, and above all:
- the user's decisions block at the top (D-29), which overrides the text below it;
- §H.2.1 (the marker);
- §H.3.4 (`history_state`);
- §H.5.1 (the columns), §H.5.5 (existing watches);
- §H.10 (settings) and §H.11 (the migration);
- the PW-13a row of §H.13.

This task builds **only the foundations**. The collector, the planner, the guards, the API and the
UI are PW-13b to PW-13e. Nothing in this task may change what the running system does: no reader
filters on `origin` yet, and no watch is ever `READING`.

## Build

1. **Migration `0029_pw_history_bootstrap`**, `down_revision = "0028"`. That is T-134's
   `0028_default_search_cadence`, which is merged before this task starts. Check `alembic heads`
   shows one head. It must do exactly what §H.11 lists:
   - the `property_watch` columns of §H.5.1;
   - `pw_bucket.origin` (`'LIVE'` for every existing row);
   - the `CHECK`s;
   - `last_status_known` recreated with `READING_HISTORY` (the `0027` pattern);
   - no server default left on any new column;
   - existing watches `NOT_REQUESTED` with `history_days` NULL (§H.5.5);
   - the downgrade as §H.11 says: history rows deleted first, then the columns and `CHECK`s,
     with `READING_HISTORY` statuses set to NULL before the old `CHECK` is restored.
   - **`pw_bucket` is a TimescaleDB hypertable.** Find out from the code and migrations whether
     compression is configured on it (`0026`, `app/services/timescale*.py`). Test the column add on
     a hypertable with data, and on one with a compressed chunk if compression can apply. Report
     the TimescaleDB version you tested on.
2. **Models** (`backend/app/models/property_watch.py`): the columns, `PwBucket.origin`, and the
   `CHECK`s in `__table_args__`, so that `alembic check` reports no drift.
3. **Enums** (`backend/app/models/enums.py`):
   - `PwWatchStatus.READING_HISTORY`;
   - `PwHistoryState` (`NOT_REQUESTED`, `READING`, `COMPLETE`, `PARTIAL`, `UNAVAILABLE`);
   - `PwBucketOrigin` (`LIVE`, `HISTORY`).
   The SQL `CHECK` literals are pinned to the enums by a parity test, as the existing PW enums are.
   `AnomalyType` and every Phase A enum stay unchanged (AGENTS.md rule 6).
4. **Settings** (`backend/app/core/config.py`): the eight `pw_bootstrap_*` settings of §H.10, with
   their bounds and the cross-checks.
   - **`pw_bootstrap_enabled` defaults to `true` (D-29).**
   - The cross-checks apply where `pw_collection_enabled` is on, as the other PW cross-checks do.
     They include `pw_new_value_history_days <= pw_bootstrap_days <= pw_baseline_lookback_days`,
     and `pw_bootstrap_days × 24 + pw_bootstrap_max_hours < pw_metric_retention_days × 24`.
   - Each refusal names both settings and their values, in the style of `config.py` ~L906–945.
5. **The collector writes `origin = 'LIVE'` explicitly** on every bucket it stores
   (`backend/app/collectors/pw_collector.py`, the bucket row builder). This is the only collector
   change in this task.
6. **Docs:** none. PW-13g does them.

## Tests

- **Migration (integration):**
  - the up → down → up round trip with data carried (a watch, `LIVE` buckets and a
    `READING_HISTORY` status present before the downgrade);
  - `alembic check` is clean;
  - every `CHECK` accepts its values and refuses an unknown one;
  - old rows read `origin = 'LIVE'` and `NOT_REQUESTED`;
  - no server default remains (the `0010` test pattern in `tests/integration/test_migrations.py`);
  - the hypertable cases;
  - the downgrade deletes `HISTORY` rows and their metrics, and keeps `LIVE` rows.
  - Build Alembic `Config()` **without** `alembic.ini`, as `test_migrations.py` does (AGENTS.md §4).
- **Settings (unit):** each default (`pw_bootstrap_enabled` true), each bound, each cross-check at
  its edge and one past it.
- **Enum/CHECK parity (unit).**
- **The collector writes `LIVE`:** extend an existing PW collection test.
- Break each guard once and report which test failed.

## Gates

From `backend/`, with the main checkout's venv and `PYTHONPATH=.`:
- `ruff check app tests` and `mypy app` (0 errors);
- the whole unit suite;
- alembic `upgrade head → downgrade -1 → upgrade head` and `alembic check`;
- the **whole integration suite in one session** (`-m integration tests/integration/`) on
  `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw13a`.

Copy each summary line verbatim. Do not use `-q` twice.

## Allowed files

- New: `backend/alembic/versions/0029_pw_history_bootstrap.py`.
- `backend/app/models/property_watch.py`, `backend/app/models/enums.py`, `backend/app/core/config.py`.
- `backend/app/collectors/pw_collector.py` (the `origin` field only).
- Tests under `backend/tests/`.
- Your report: `docs/tasks/PW-13a.report.md`.

Any other file: name it and say why in your report. Do not touch it.
