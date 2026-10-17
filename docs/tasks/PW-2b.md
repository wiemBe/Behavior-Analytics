# Task PW-2b — Phase G collection: scope resolution, admission, persistence, the dispatch and retention tasks

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 3 (only admitted and
pinned value text persists), 7 (no migration in this task), 8 (fail closed) and **9 (per-run SQL
cost stays constant: bulk loaders, never a query per watch, per value or per tuple)**, and the §4
test rules, including the integration rules of 2026-10-17. Then read
`docs/PHASE-G-PROPERTY-WATCH.md` (binding). PW-1 (schema, settings, facts), PW-2a (provider
`get_pw_keyed`, `aql.py`, `fold.py`, `keyed_search.py`) and PW-3a (pure detection) are merged:
build on them and do not redefine them. Read `docs/tasks/PW-2a.report.md` §4 (its open points 2
and 6 are yours).

## What this task builds (design sections)

- **§G.4 resolution:** a pure resolver (source, type, group with descendants; **cycle-safe** walk
  with a visited set, the lab's two groups name each other as parent; chunking at
  `pw_max_sources_per_search`; `SCOPE_EMPTY`, `SCOPE_STALE` from the group row's `synced_at`
  against `pw_scope_stale_after_hours`, `SCOPE_TOO_LARGE`; the scope fingerprint and member
  count; the `scope_changed` flag for the first bucket under a new fingerprint and the
  `pw_scope_change_grace_buckets` after it), fed by **one** bulk read of the instance's groups and
  sources for every group-scoped watch.
- **§G.6 "The family and the tick":** `dispatch_property_watch_collection` (the name
  `app/anomaly/capabilities.py` already uses) in the `dispatch_ips_collection` shape
  (`app/workers/tasks.py:1335`): it claims the `PROPERTY_WATCH` cadence slot, is a no-op while
  `pw_collection_enabled` is false, and per instance runs one plan under one `PwSearchBudget`;
  watermarks (`collected_through` advances only past completed buckets; the **first run seeds the
  watermark at the latest closed bucket and replays nothing**); settle 120 s. Collection only:
  evaluation and training are PW-3b, which adds them to the same pipeline. Add the beat entries
  next to the IPS ones (`app/workers/celery_app.py:169-177`).
- **§G.7.3 admission and retirement** (pure, then bulk): `pw_min_admission_events`, the quotas
  and the ceiling, `admission_truncated` and the admission counters on the bucket row, tracking
  windows, retirement; pinned values are always tracked.
- **Persistence** of buckets (with `query_provenance`, the admission counts, `scope_*`,
  `in_maintenance`), values and sparse metrics: **one statement per table per tick**.
- **§G.7.2 retention:** buckets and metrics after `pw_metric_retention_days`, a value row after
  retirement and its last metric aged out, in bounded batches; `apply_property_watch_retention`
  with its beat entry (the `apply_ips_retention` shape).
- `app/providers/factory.py` passes `pw_max_sources_per_search` to the providers (PW-2a open
  point 2).

Out of scope: evaluation, training, the lifecycle, `all_detectors()` (PW-3b); alerting (PW-4);
the API (PW-5); a migration (none in this task; if the schema lacks something, stop and report).

## Tests

- Unit (§G.15 "Scope"): source, type and group resolution; descendants; the two-group parent
  cycle terminates, visits each group once, resolves the union; chunking at the cap; the three
  scope states (with the swallowed group-list failure simulated for `SCOPE_STALE`); a fingerprint
  change opens the grace window. Admission: threshold, quotas, ceiling, truncation flag, pinned
  always tracked, retirement.
- Integration (your own database, below): collection end to end through the **mock provider**
  (it runs the real fold); the watermark seeding and no replay; a failed search holds the
  watermark and writes `NOT_COLLECTED`, never zero; a spent budget; the dispatch gate (no-op
  while disabled, no-op while the family is off); retention in bounded batches; **statement-count
  pins** in the `test_collection_at_the_default_ceiling_is_bulk` style: collection at 1 and 10
  watches and at 50 and 200 values gives the same count; report the number.
- Privacy: a value that never reached admission is found in no table after a collection.
- Break each guard once and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, and the **whole** integration suite
once on your database with the log kept (`-m integration tests/integration/ -ra`; read the skip
count):
`TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw2b`.

## Allowed files

- new: `backend/app/property_watch/scope.py`, `backend/app/property_watch/admission.py`,
  `backend/app/collectors/pw_collector.py`, `backend/app/services/pw_pipeline.py`,
  `backend/app/services/pw_retention.py`, `backend/app/repositories/property_watch.py`
- `backend/app/workers/tasks.py`, `backend/app/workers/celery_app.py` (the PW tasks and beat
  entries only), `backend/app/providers/factory.py`
- `backend/app/property_watch/fold.py` and `keyed_search.py` only to fill the admission and
  provenance fields PW-2a left at their defaults
- new test files under `backend/tests/unit/` and `backend/tests/integration/`
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-2b]` markers in §G.4, §G.6, §G.7.2, §G.7.3,
  §G.14.2
- `docs/tasks/PW-2b.report.md` (new)

A parallel task (PW-5, the API) owns `app/api/`, `app/schemas/`, `app/security/rbac.py`,
`app/services/seed.py`, `app/services/property_catalog.py` and new files named
`*property_watch_api*` or `property_watches*`: do not touch them.

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/pw2b/`, never `/tmp`.
