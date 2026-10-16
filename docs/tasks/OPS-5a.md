# Task OPS-5a — TimescaleDB retention and compression policies actually run

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/OPERATIONS-DESIGN.md`:
- §5, the OPS-5 part about `apply_policies`;
- Appendix A's last paragraph;
- §10.2's OPS-5 line.

## Why (a verified defect)

`app/services/timescale.py:49` `apply_policies` reconciles retention and compression policies for
the hypertables it covers (`timescale.py:23`: `log_source_metric`, `search_result_metric`,
`rule_metric`, `offense_snapshot`). **Nothing calls it.** No route, worker, CLI or startup code
does, and no migration adds a TimescaleDB policy. So the retention and compression settings
(`RETENTION_*_DAYS`, `COMPRESSION_AFTER_DAYS`, gated by `RETENTION_ENABLED`) do nothing today. In
production these tables grow without bound. The daily deleter tasks for WAF, IPS, catch-all, geo,
custom rules and search results are separate, and they already run on the beat.

## Build

1. A Celery beat task, about daily, in the pattern of the existing retention tasks in
   `backend/app/workers/celery_app.py` and `tasks.py`, with an interval setting in their style.
   - It opens a session, calls `apply_policies`, and logs the per-table outcome, without
     secrets.
   - It is a no-op that logs why when `RETENTION_ENABLED` is false. **Keep the default false:**
     turning it on is the operator's choice, which the installer (OPS-4) writes. Check what the
     design says the installer writes.
2. **Run once at startup too**, if the design calls for it, so a changed setting takes effect
   without waiting a day.
   - Do it from the worker or beat, not from the API process, because several API replicas
     would race.
   - It must be idempotent: running it twice leaves the same policies.
3. **Read `apply_policies` critically.**
   - Does it add, update and remove policies correctly when a setting changes, for example when
     retention changes from 90 to 400 days, or when it is turned off?
   - Is each table's call isolated, so one failure does not stop the others?
   - Fix what is wrong in `timescale.py`, and say what you found.

## Tests

- Unit: the task is a no-op when disabled, and it calls `apply_policies` when enabled.
- **Integration, on your own DB:**
  - With `RETENTION_ENABLED=true`, running the task creates a retention job and a compression
    job for each covered hypertable. Assert them through `timescaledb_information.jobs`.
  - A second run creates no duplicate.
  - Changing a retention setting and running again changes the job's `drop_after`.
  - Turning retention off removes the job, or does what the design says. Pin that behaviour.
- Break each new test once, then restore the code.

## Gates

From `backend/`, with `PYTHONPATH=.` and the main checkout's venv:
- `ruff check app tests`, clean;
- `mypy app`, 0 errors;
- the unit tests;
- integration on `postgresql+asyncpg://test:test@localhost:5433/qradar_obs_ops5a`: your new
  file, then `tests/integration/` in full, once.

Copy each summary line verbatim.

## Allowed files

`backend/app/services/timescale.py`, `backend/app/workers/celery_app.py`,
`backend/app/workers/tasks.py`, `backend/app/core/config.py` (the interval setting only),
`.env.example` (the new setting), new tests, `docs/tasks/OPS-5a.report.md` (new). Do not commit.
Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops5a/`, never `/tmp`.
