# Task PW-3b — Phase G detection, the engine half: loaders, the lifecycle, the pipeline step, training, cost pins

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 3, 6, 8 (fail
closed; **the verdict scope comes from the spec in `app/anomaly/capabilities.py`, never from the
detector**) and **9 (per-run SQL cost stays constant: bulk loaders, never a query per watch, per
value or per tuple)**, and the §4 test rules, including the integration rules of 2026-10-17. Then
read `docs/PHASE-G-PROPERTY-WATCH.md` (binding): §G.8.0 in full (the reading table, baselines,
learning period, lifecycle), §G.8.2, §G.8.3, §G.9.3, §G.12, §G.14.2 and §G.15. PW-1 (models,
settings, `facts.py`), PW-2a, PW-2b (collection, `services/pw_pipeline.py`,
`repositories/property_watch.py`) and PW-3a (the pure `baseline.py`, `detectors.py`,
`history.py`, `inputs.py` and `PW_DETECTORS`) are merged: build on them, do not redefine them.
Read `docs/tasks/PW-3a.report.md` §6 and `docs/tasks/PW-2b.report.md` (their open points marked
for PW-3b are yours).

The shape to follow is the IPS engine and pipeline: `app/ips/engine.py` (`IpsAnomalyEngine`: bulk
loads, `_apply_hysteresis`, `_apply_transition`, `_record_transition`, one active incident per
subject), `app/services/ips_pipeline.py` (collect, then evaluate from an evaluation watermark,
then train) and the DB half of `app/ips/baseline.py` (`rebuild_due`).

## What this task builds

- **The engine** (`app/property_watch/engine.py`): for each closed bucket after the evaluation
  watermark (at most `pw_max_evaluation_buckets_per_run` per run), one bulk load of everything the
  five detectors need for **all** enabled watches of the instance: the buckets, tracked values and
  their readings (the §G.8.0 reading table through `facts.py`, never a hand-rolled zero), the
  baseline cells for that hour of the week, the detector states, the open incidents, the novelty
  history (`history.py`) and the `PW_NEW_COMBINATION` component lookup. Then the pure detectors
  under the scope from `verdict_scope(spec, …)`, then **one write statement per table**: states,
  anomalies, transitions, the watch status.
- **The lifecycle** (§G.8.0): `INSUFFICIENT_DATA → NORMAL → CANDIDATE → OPEN → RECOVERING →
  RESOLVED`, a transition row per change, one active incident per (watch, detector, `value_key`),
  an unknown or incomplete reading **holds** state, a missing bucket or failed collection never
  resolves an incident. Confirmation and recovery from `anomaly_open_after_intervals` and
  `anomaly_resolve_after_intervals`: this is where the spike's "required consecutive buckets"
  lives (PW-3a open point 2). Disappearance keeps its own `pw_disappear_buckets` rule inside the
  detector; do not count it twice — state in the report how the two combine.
- **`watch_version`** (§G.3, §G.12): readings, baselines, history and states of another version
  are never compared; an active incident under an older version is **resolved** at the next run
  with a transition whose reason says the watch changed (PW-4 later resolves its alert; leave no
  placeholder, say in the report where it hooks in).
- **Training after evaluation** in the same run (§G.8.0 "Baselines"): at most
  `pw_baseline_values_per_run` values per run, chosen the way IPS `rebuild_due` chooses (least
  recently computed first), the pure builder's cells and exclusion tally upserted, 5 statements per chunk; cells
  under `baseline_min_samples` stored and never driving a verdict.
- **`PwThresholds` from settings** (PW-3a open point 5), in the `IpsAnomalyEngine._thresholds`
  shape: `deviation_z` from `anomaly_deviation_threshold`, `mad_floor_ratio` from
  `baseline_mad_floor_ratio`, `min_coverage` from `pw_min_coverage`, `baseline_min_samples`, and
  every `pw_*` threshold. A unit test fails if a `PwThresholds` field has no source.
- **The pipeline step:** `services/pw_pipeline.py` runs collect → evaluate → train per instance
  under its existing lock; evaluation reads only buckets that collection completed; a collection
  failure leaves evaluation where it was.
- **`all_detectors()` includes `PW_DETECTORS`** (PW-3a open point 4), now that
  `dispatch_property_watch_collection` has its beat entry: update `EXPECTED_STATUS` in
  `tests/unit/test_detector_capabilities.py` **additively** and make
  `test_engine_and_registry_agree_on_what_is_executable` and
  `test_scheduled_tasks_exist_in_the_beat_schedule` pass for PW without weakening either.
- **Maintenance** (PW-3a open point 1): the design gives no verdict exit for maintenance; keep
  it so. Maintenance buckets are excluded from training (the pure builder already tallies them);
  alert suppression is PW-4's.

Out of scope: alerting, fingerprints, `AlertService` (PW-4); the API (PW-5/5b); F.1 (PW-6); any
migration (none in this task; if the schema lacks something, stop and report).

## Tests

- Integration (your own database, below), end to end through the **mock provider**:
  `INSUFFICIENT_DATA` to `RESOLVED` for the spike; drop and disappearance on measured zeros, and
  **never** on unknown zeros or a `LOWER_BOUND` bucket; an unknown bucket holding state; a failed
  collection never resolving; one active incident per (watch, detector, `value_key`); a
  `watch_version` bump resolving incidents and starting fresh baselines; the learning period (no
  verdict below 8 samples, never a silent `NORMAL`); `PW_NEW_VALUE` and `PW_NEW_COMBINATION` with
  complete and incomplete history.
- **Statement-count pins** (the `test_waf_scale` / `test_ips_scale` shape): collection plus
  evaluation at 1 and 10 watches and at 50 and 200 values give the **same** count, **at most 40**;
  the component lookup adds one statement only when a tuple is in its window; training scales with
  chunks, not values. Report the numbers.
- Unit: the threshold wiring; the hysteresis and transition rules on synthetic states.
- Break each guard once (fail-closed exits, version check, the consecutive-bucket rule, the bulk
  loaders) and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, and the **whole** integration suite
once on your database with the log kept (`-m integration tests/integration/ -ra`; read the skip
count):
`TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw3b`.

## Allowed files

- new: `backend/app/property_watch/engine.py`, `backend/app/property_watch/training.py`
- `backend/app/services/pw_pipeline.py`, `backend/app/repositories/property_watch.py` (bulk
  loaders and writes), `backend/app/anomaly/capabilities.py` (`all_detectors()` only),
  `backend/app/workers/tasks.py` (only if the pipeline's task signature must change)
- `backend/app/property_watch/detectors.py`, `baseline.py`, `history.py` **only** to fix a defect
  you find, each named in the report
- `backend/tests/unit/test_detector_capabilities.py` (additive), new test files under
  `backend/tests/unit/` and `backend/tests/integration/`
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-3b]` markers in §G.8.0, §G.8.2, §G.8.3, §G.9.3,
  §G.14.2
- `docs/tasks/PW-3b.report.md` (new)

Do not touch `app/api/`, `app/schemas/` or the PW-5 files. Do not commit. Scratch under
`/home/efe/Documents/QRadar-Dash-work/pw3b/`, never `/tmp`.
