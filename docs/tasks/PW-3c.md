# Task PW-3c — Phase G backend follow-ups: incidents that can never end, stale baseline cells, the scope staleness rule, a disabled watch's alerts

Read `AGENTS.md` at the repository root first and follow it exactly (§2 rules 8 and 9; the §4
test rules, including the integration rules of 2026-10-17 and "test the path the operator takes").
Then read `docs/PHASE-G-PROPERTY-WATCH.md` §G.4, §G.7.2, §G.8.0, §G.9, §G.10.1, §G.15 (binding),
and the open points these items come from: `docs/tasks/PW-3b.report.md` §6, `PW-4.report.md`
§6 (in the PW-4 commit message and the PLANNER row if the file is short), `PW-5b.report.md` §5.

**Base:** `agent/pw-merge` (all Phase G branches merged except PW-6b). PW-4's alert paths exist:
`PwAnomalyEngine` (`app/property_watch/engine.py`, `_resolve_old_versions`) resolves an incident
and its alert together; `resolve_watch_alerts` (`app/services/property_watches.py`) resolves a
watch's open alerts by `context.watch_id`. Reuse them; add no second way to resolve.

## Build

1. **A retired value's open incident** (PW-3b §6). Admission retires a tracked value unobserved
   for `pw_value_active_days` (`app/property_watch/admission.py`, run by the collector). An
   incident open on it then never ends, because a retired value is no longer judged. When a value
   is retired, its OPEN or RECOVERING incidents resolve, with their alerts when alerting is on,
   actor `pw-engine`, reason `value retired; no longer evaluated`. Decide where it runs (the
   collector's retirement write or the engine's next run) by the statement budget: no query per
   value; say which and why.
2. **A value deleted by retention** (`app/services/pw_retention.py`, PW-3b §6). Retention deletes
   a retired value only after its retirement is older than the cutoff and it has no metric left;
   with item 1 its incidents are already resolved. Pin that: a retention run never deletes a
   value whose incident is still OPEN or RECOVERING (fail closed: skip it, count it in the report).
3. **Stale baseline cells** (PW-3b §6). `PwBaselineTrainer` upserts only the cells it
   recomputed; a cell whose samples all aged out keeps its last statistics, so a detector can
   judge on old data. A cell whose window now holds fewer samples than the reliability minimum is
   written as not reliable (or deleted), in the same bounded statements. Detectors then read
   `INSUFFICIENT_DATA` for it. Pin the statement count per chunk as unchanged.
4. **Scope staleness on the oldest walked row** (PW-5b §5 point 2). `resolve_scope`
   (`app/property_watch/scope.py:196`) decides `SCOPE_STALE` on the watch's own group row, while
   `/scope` reports the oldest walked row's age; an interrupted sync can leave a descendant older
   than the limit and the scope unmarked. Decide staleness on the **oldest** `synced_at` of the
   walked groups (fail closed); the collector and the API then agree. Test the interrupted-sync
   case at both the resolver and the `/scope` route.
5. **A disabled watch's alerts** (PW-4 §6 point 2). A `PATCH` that sets `enabled` to false (the
   route at `app/api/routes/property_watches.py`, `PATCH /{watch_id}`) resolves the watch's OPEN
   and RECOVERING incidents and their alerts, actor the principal, reason
   `watch disabled; no longer evaluated`, through the paths above, in the same transaction as the
   disable. Enabling again starts fresh. A `PATCH` that leaves `enabled` alone resolves nothing.
6. **`property-options` statement count** (PW-5b §5 point 4): pin it constant at 2 and 20 groups
   and 10 and 200 sources.
7. The `dispatch_property_watch_collection` docstring in `app/workers/tasks.py` (it still says
   "Collection only"); nothing else in that file.

## Tests

Unit and integration for each item, every guard and exit path, each through the command or route
the operator or the worker runs (the pipeline for 1 and 3, the retention task for 2, the `PATCH`
route for 5, `/scope` and the collector for 4). Break each guard once and confirm a test fails;
restore. Shadow mode (alerting off) resolves incidents and touches no alert row.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, and the **whole** integration suite
once on your database with the log kept (`-m integration tests/integration/ -ra`; read the skip
count; do not pass `-q`, the repository's `addopts` already does, and a second one hides the
summary line): `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw3c`.

## Allowed files

- `backend/app/property_watch/engine.py`, `training.py`, `admission.py`, `scope.py`
- `backend/app/collectors/pw_collector.py`, `backend/app/repositories/property_watch.py`,
  `backend/app/services/pw_retention.py`, `backend/app/services/pw_pipeline.py`
- `backend/app/services/property_watches.py`, `backend/app/api/routes/property_watches.py` (the
  disable path only)
- `backend/app/workers/tasks.py` (the one docstring)
- new test files; additive edits to existing PW test files where an exact assertion must learn
  the new behaviour (list each in the report, with why it is not a weakening)
- `docs/PHASE-G-PROPERTY-WATCH.md` (`[BUILT — PW-3c]` markers and the sentences these rules
  change); `docs/tasks/PW-3c.report.md` (new)

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/pw3c/`, never `/tmp`.
