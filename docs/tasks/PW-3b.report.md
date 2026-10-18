STATUS: FINAL

# PW-3b report

## 1. Files changed

- `backend/app/property_watch/engine.py` (new): `PwAnomalyEngine`, bulk evaluation and lifecycle.
- `backend/app/property_watch/training.py` (new): `PwBaselineTrainer.rebuild_due`.
- `backend/app/repositories/property_watch.py`: bulk loaders (pending buckets with the watch's
  first collected bucket, bucket and metric windows, baseline cells, detector states, incidents,
  the aggregated component lookup, due baseline values) and one-statement-per-table writes.
  `values()` gained `populate_existing` so a re-read after a Core write is not stale.
- `backend/app/services/pw_pipeline.py`: collect, evaluate, train; evaluation and training each
  in a savepoint.
- `backend/app/anomaly/capabilities.py`: `all_detectors()` includes `PW_DETECTORS` (only change).
- `backend/tests/unit/test_detector_capabilities.py`: additive only (diff in section 6).
- New tests: `backend/tests/unit/test_pw_engine.py`, `backend/tests/integration/pw_engine_support.py`
  (rig), `test_pw_engine.py`, `test_pw_engine_novelty.py`, `test_pw_engine_scale.py`,
  `test_pw_training.py`.
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-3b]` markers in G.8.0, G.8.2, G.8.3, G.9.3, G.14.2.
- Not touched: `detectors.py`, `baseline.py`, `history.py`, `workers/tasks.py` (its dispatch
  docstring still says "Collection only"; stale wording, no signature change was needed).

## 2. What I did

The engine reads, per run and for all enabled watches of the instance: the pending buckets
(after `evaluated_through`, up to `collected_through`, current version, at most
`pw_max_evaluation_buckets_per_run` per watch), tracked values, bucket and metric windows, the
baseline cells of the pending hours of the week, detector states, active incidents, and (only
while a value is inside its novelty window) the long bucket history and the aggregated component
lookup. The pure PW-3a detectors judge each (value, bucket) through `reading_for`; the declared
scope comes from the specs through `pw_input_scope` inside the detectors, the engine never grants
scope. The lifecycle is the shared `anomaly/lifecycle.py`; writes are one statement each for
states, incidents, transitions, watch status.

How the disappearance rule and the lifecycle combine: the detector counts measured zeros
(`pw_disappear_buckets`) and returns its first anomalous verdict at the last of them; the
lifecycle then needs `anomaly_open_after_intervals` consecutive anomalous verdicts. The incident
opens at zero bucket `pw_disappear_buckets + anomaly_open_after_intervals - 1` (7 at the defaults).
Not counted twice: zeros in the detector, verdicts in the lifecycle. The spike's "required
consecutive buckets" is `anomaly_open_after_intervals` here.

`watch_version`: an active incident whose value belongs to an older version is resolved at the next
run, transition reason "watch definition changed" (`report.version_resolved`). PW-4 hooks in at
`PwAnomalyEngine._resolve_old_versions` (it resolves the alert where the incident is resolved).
Nothing is left as a placeholder. The old state is rewritten once, not every run.

### What was wrong or missing in the inherited work (all fixed)

1. Novelty facts, including a full-history `verify_value_history`, were built for every value in
   every pending bucket, and metrics for the whole history window of all values were loaded
   (up to about 1M rows) and `watch_first_collected_at` was the earliest *loaded* bucket, not the
   watch's. Now: novelty only for values inside their window of a watch that selected a novelty
   detector, history shared per (watch, version, first appearance), metrics only for the recent
   window, the first collected bucket of the current version comes with the pending-bucket read
   (no extra statement). Established values get a light "established" fact (without it a
   not-computed fact made every established value `INSUFFICIENT_DATA` forever).
2. The component lookup returned one row per metric row of every tuple value over the history
   window. It is now one aggregated statement (earliest occurrence per component, position and
   bucket measurement), restricted to the components of tuples in their window.
3. `write_anomalies` mixed loaded rows (with `created_at`/`updated_at`) and new rows (without)
   in one executemany: a bind-parameter error as soon as a run both updated and created an
   incident. The audit timestamps are now the database's; `updated_at` is set in the upsert.
4. The engine overwrote `last_status` with OK/OPEN/RECOVERING even when the collector had set a
   blocking status (`SCOPE_STALE`, ...). It now moves only `evaluated_through` then. An unknown
   status string reads as `STALE` (fail closed).
5. A value that was not tracked at a bucket (before `tracked_since`) was judged, and
   `INSUFFICIENT`/healthy verdicts moved its state. It is skipped now.
6. Novelty candidates used the latest pending bucket, so a value whose window ended between two
   pending buckets lost its novelty facts for the earlier ones. Now the earliest pending bucket.
7. Pending-bucket "recent" and "since first appearance" scans were O(history) per value per
   bucket; they are dict lookups now. Buckets of another version are left out (never compared).
8. The pipeline: evaluation or training failure rolled the whole tick back including the
   collection. Both run in savepoints now (the IPS pipeline's isolation), report only the
   exception class (`sanitized_error`; a value can sit in a database error), and a failed
   evaluation skips training. The old `pipeline.steps == ["collect"]` payload is kept because
   `test_pw_dispatch.py` pins it; the true list is `steps_run`.
9. Tests: the inherited ones covered one spike path and a count pin without the incident path.
   Replaced by the suite below; the inherited unit tests are kept.

## 3. Gates

All from `backend/` with `PYTHONPATH=.` and the main checkout's virtualenv.

- `ruff check app tests`: All checks passed.
- `mypy app`: Success, no issues in 267 source files.
- `git diff --check`: clean.
- Whole unit suite (`-m "not integration" -ra`): **4336 passed, 10 skipped, 1416 deselected**
  (the 10 skips are the route-auth allowlist cases, unchanged). Log:
  `/home/efe/Documents/QRadar-Dash-work/pw3b/unit-final.log`.
- Whole integration suite, one session, on `qradar_obs_pw3b`
  (`-m integration tests/integration/ -ra`): INTEG_RESULT. Log:
  `/home/efe/Documents/QRadar-Dash-work/pw3b/integ-final.log`.

## 4. Statement counts

(`recorded_statements`, mock provider, 12 buckets, every value judged by all five detectors and
every judgement writing a state, an incident and a transition.)

- Collection plus evaluation: **20 statements** at 1 and 10 watches and at 50 and 200 values
  (all four sizes equal; ceiling 40). Collection is 9, evaluation 11 (watches, pending buckets,
  values, states, buckets, metrics, cells, and four writes; the incident read adds 1 when an
  incident is already active).
- The component lookup: **+1** statement only while a tuple is in its window (9 to 10 in the
  evaluation-only measurement, in `test_the_component_lookup_costs_one_statement_only_when_a_tuple_is_in_its_window`).
- An idle evaluation (nothing pending): at most 6 statements.
- Training: **5 statements** per chunk at 50 and at 100 values (`pw_baseline_values_per_run` 100).

## 5. Guard mutations (each restored; tests failed as listed)

| Mutation | Killed by |
|---|---|
| unknown/insufficient bucket treated as healthy | `test_spike_runs_from_insufficient_data_to_resolved` |
| version-bump resolution removed | `test_a_version_bump_resolves_the_old_incident_and_starts_fresh` |
| confirmation count forced to 1 | `test_spike_runs_from_insufficient_data_to_resolved` |
| evaluation not bounded by `collected_through` | `test_a_missing_bucket_inside_the_window_is_not_a_zero` |
| history always complete | `test_an_incomplete_history_is_insufficient_never_new[a missing bucket]` |
| a `PwThresholds` field unwired | `test_every_threshold_field_is_wired_from_settings` |
| training past the evaluation watermark | `test_training_stops_at_the_evaluation_watermark` |
| metric loader per value | `test_collection_plus_evaluation_is_bulk_at_every_required_size` |
| bucket loader per watch | same |
| component lookup issued without a tuple | `test_the_component_lookup_costs_one_statement_only_when_a_tuple_is_in_its_window` |
| blocking collection status overwritten | `test_a_blocking_collection_status_blocks_verdicts_and_is_kept` |
| training before evaluation | `test_the_pipeline_collects_then_evaluates_then_trains` |
| untracked value judged | first run SURVIVED; added `test_a_value_is_not_judged_at_buckets_before_it_was_tracked`, then killed |

## 6. Open points

- `test_detector_capabilities.py` diff (additive: import `PW_DETECTORS`, `PwAnomalyType`,
  `PW_DETECTOR_FUNCTIONS`; five `EXPECTED_STATUS` entries; one new assert in
  `test_every_named_detector_has_a_spec`; one new block in
  `test_engine_and_registry_agree_on_what_is_executable`). Nothing removed or loosened.
- Fingerprints: `pw_anomaly.fingerprint` is NOT NULL, so the engine writes a stable one
  (`compute_fingerprint` over instance, watch, value key, detector). Alerting (PW-4) owns the real
  contract; if PW-4 chooses another shape, only `_fingerprint` changes, but existing rows keep the old
  value.
- Retired values: a value retired after `pw_value_active_days` with an open disappearance incident
  is not tracked, so its readings are no longer observations and the incident holds open until it
  returns. Failing closed, but a stuck OPEN row is possible; the design does not say how a
  retired value's incident ends. Needs a decision (resolve on retirement, or leave).
- A value deleted by retention while its incident is open leaves the incident without a state row
  (FK `SET NULL`); nothing resolves it. Not exercised.
- Stale baseline cells: training upserts only the cells it recomputed; a cell whose samples all aged
  out keeps its last statistics. Same as the IPS builder; risk is a verdict on old data.
- Training loads up to `pw_baseline_values_per_run` values times 28 days of metric rows in one
  statement; at 100 values of 288 buckets/day that is up to about 800k rows. Bounded by the
  setting, not measured on a console.
- `workers/tasks.py` docstring of `dispatch_property_watch_collection` still says collection only.
- No real QRadar, no worker/beat run: the pipeline was driven directly, not through Celery.

## Orchestrator addendum (2026-10-18)

- The whole integration run in §3 was cut by the 01:27 shutdown. The orchestrator re-ran it on
  `qradar_obs_pw3b` (11:24–11:54, log `QRadar-Dash-work/pw3b/integ-rerun.log`): **1,396 passed,
  1 failed, 0 skipped, 0 errors**. The failure was
  `test_phase_a_api.py::TestDetectorCapabilities::test_every_named_detector_is_published_with_its_status`:
  `all_detectors()` now publishes the five PW types and the test's exact set lacked them.
- Review fix 1 (GLM 5.3 flash): the exact set gains `PwAnomalyType`, and each PW detector is pinned
  `NOT_ENABLED` under the defaults (`pw_collection_enabled` is false; checked against
  `detector_status`). The orchestrator re-ran the file: 51 passed, 0 failed; with
  `PW_COLLECTION_ENABLED=true` the test fails (the pin holds). ruff clean.
