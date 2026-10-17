# PW-3a report

## 1. Files changed
- `backend/app/anomaly/capabilities.py`: `BehaviorDomain.PW_VALUE`, `PW_BASELINE_METRICS`, `PW_DETECTORS` (five specs, requirements per §G.8.2/§G.8.3/§G.9.3, setting `pw_collection_enabled`), `pw_input_scope`, `PW_VALUE` branch of `detector_status`. Verdict arithmetic reused from `app/waf/measurement.py`. PW is not in `all_detectors()` (no Beat task exists until PW-3b).
- New: `backend/app/property_watch/{inputs,baseline,history,detectors}.py`.
- New: `backend/tests/unit/test_property_watch_detection.py` (89 tests).
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT - PW-3a]` notes in §G.8.0, §G.8.2, §G.8.3, §G.9.3 (corrected, see below).
- `docs/tasks/PW-3a.report.md`.

## 2. What was wrong in the inherited (Codex) work, and the fix
1. A bucket in maintenance returned `INSUFFICIENT_DATA`. The design lists no such verdict exit (maintenance only excludes from training; suppression is alerting's). Removed from the verdict path; test added. The doc note had claimed it, now corrected.
2. `bucket.property_not_parsed` (`NOT_APPLICABLE`, §G.12) was not an exit for the three baseline verdicts. Added to the shared bucket guard.
3. Disappearance: a missing bucket inside the 6-bucket window just ended the streak and returned HEALTHY ("k consecutive"). Fail-closed violation. Now `INSUFFICIENT_DATA`. An unknown `last_seen_at` was also HEALTHY; now `INSUFFICIENT_DATA` (a stale known `last_seen_at` stays HEALTHY).
4. Learning message computed the day from `first_seen_at`, not the bucket; fixed ("day N of 7", capped).
5. Baseline builder counted `NOT_AN_OBSERVATION` readings as `unknown` exclusions and as candidates; they are now skipped (not a sample, not an exclusion).
6. Tests were thin: the monotonicity test only asserted "always fires", there was no converse, no test of mid-window failures, `NOT_APPLICABLE`, cell version, thin-but-flagged-reliable cell, duplicates/width in history, or the combination's shared novelty exits. Added (class `TestReviewedGaps`). One existing test (`3 consecutive`) was adjusted because its old input (a short `recent`) is now correctly `INSUFFICIENT_DATA`; it now supplies a non-zero reading instead.
Checked and found correct: exit order of spike/drop/disappearance/new value (incl. established before history, flood after history), requirements table, exclusion tally order, history failures, capability truth, exclusivity of the two novelty verdicts.

## 3. Per design section
- G.8.0: specs, `detector_status`, mapping of `PwBucketMeasurement` to `WafMeasurement` (`inputs.py`), baseline builder with the full tally (weekday x hour, median/MAD/p05/p95, thin cells stored with `is_reliable=False`, never used).
- G.8.2: three verdicts; spike `LOWER_BOUND_PROVES_INCREASE`, drop/disappearance `EXACT` volume with coverage `LOWER_BOUND_PROVES_INCREASE`.
- G.8.3: `verify_value_history`, `detect_new_value` (learning, membership, pinned, established, history, flood, materiality).
- G.9.3: `detect_new_combination`, exclusive with `PW_NEW_VALUE` (the other returns HEALTHY with the reason), lower-bound component history proves presence, below-admission component falls to new value, one-property watch `INSUFFICIENT_DATA`.

## 4. Gates (from `backend/`, `PYTHONPATH=.`, main checkout venv)
- `ruff check app tests`: All checks passed.
- `mypy app`: Success, no issues in 252 source files.
- `pytest -m "not integration"`: 3972 passed, 10 skipped (route-auth allowlist skips), 1308 deselected, 0 failed.
- New file alone: 89 passed. No integration tests run (no database given).

## 5. Guard mutations (each applied, tests run, restored from a backup copy)
- drop coverage guard off: `TestDrop::test_exits_and_guards[coverage]` failed.
- `scope_changed` bucket exit off: `TestSpike::test_exits_in_guard_order[membership changed]`.
- missing window bucket no longer fails closed: `test_disappearance_fails_closed_on_every_bad_window_member`.
- flood exit off: `TestNewValue::test_every_exit[flood]`.
- window-point exactness check off: survived at first; added the lower-bound-zero case; now `test_disappearance_fails_closed_on_every_bad_window_member` fails.
- drop spec volume requirement set to `LOWER_BOUND_PROVES_INCREASE`: `TestCapabilities::test_scope_comes_from_declared_inputs`.
- history version check off: `test_each_history_failure[another watch version]`.
- baseline `scope_changed` tally off: `test_each_exclusion_is_tallied_...`.
- baseline NOT_AN_OBSERVATION skip off: `test_baseline_skips_untracked_values_...`.
- pinned-never-new off: `TestNewValue::test_every_exit[pinned values]`.
- `NOT_APPLICABLE` guard off: `test_property_parsed_on_nothing_is_not_applicable[spike]`.

## 6. Open points
- Maintenance: I followed the design (no verdict exit). If the user wants verdicts suppressed in maintenance, it must be added in the design first; as is, a maintenance bucket can yield a verdict, and PW-4 alerting must suppress it (§G.15 names that test).
- Consecutive-bucket confirmation for the spike is not in the pure detector; it is the lifecycle's job (PW-3b). If PW-3b forgets, a single bucket opens an incident only after `anomaly_open_after_intervals`, which is the shared path, so low risk.
- The drop/disappearance check coverage before their other guards, so low coverage gives `INSUFFICIENT_DATA` where the design order would give HEALTHY for a non-drop. This is the more conservative side.
- `scheduled_task="dispatch_property_watch_collection"` names a task that does not exist yet and PW is deliberately absent from `all_detectors()`; PW-3b must add both together.
- No setting the design names was found missing from `config.py` (thresholds come in via `PwThresholds`, defaults match the design; PW-3b must wire them from `pw_*` settings).
- Pure functions only: nothing was run against a database or a real stack.
