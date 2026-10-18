STATUS: FINAL

# PW-3c report — seven Phase G backend follow-ups

Base: `agent/pw-merge` (all Phase G branches merged except PW-6b). Task `docs/tasks/PW-3c.md`.
Worktree `agent/pw3c`. Nothing committed.

## Files changed

Backend (production):

- `backend/app/property_watch/engine.py` — item 1 & 5. New `retired_resolved` report field and
  `REASON_RETIRED`; extracted `_retire_state` (the one place a PW incident ends without a bucket)
  and refactored `_resolve_old_versions` onto it; added `_resolve_retired`, called after
  `_evaluate_pending` and before `_persist`; added `PwAnomalyEngine.resolve_watch_incidents` for
  the disable path.
- `backend/app/services/property_watches.py` — item 5. `resolve_watch_alerts` gains a `reason`
  parameter (default `REASON_WATCH_DELETED`, so the delete path is unchanged) and the module gains
  `REASON_WATCH_DISABLED`.
- `backend/app/api/routes/property_watches.py` — item 5. `PATCH /{watch_id}` resolves the watch's
  incidents and alerts, as the principal, when a `PATCH` turns `enabled` from true to false, in the
  same transaction; imports `PwAnomalyEngine`.
- `backend/app/services/pw_retention.py` — item 2. `_RetentionStep` dataclass; a `blocked` report
  field; the `pw_value` delete and its remaining check gain `NOT EXISTS (... pw_anomaly ... OPEN,
  RECOVERING)`; a blocked-count statement; `BLOCKING_INCIDENT_STATES` tuple.
- `backend/app/property_watch/training.py` — item 3. `_rebuild_chunk` deletes the cells of the
  chunk's values that the rebuild did not write.
- `backend/app/repositories/property_watch.py` — item 3. New `delete_stale_baselines`.
- `backend/app/property_watch/scope.py` — item 4. `resolve_scope` decides `SCOPE_STALE` on the
  oldest `synced_at` of the walked groups, and names that row's age.
- `backend/app/workers/tasks.py` — item 7. Only the `dispatch_property_watch_collection` docstring
  (it said "Collection only"; now collect → evaluate → train).

Tests:

- `backend/tests/integration/test_pw_alerting.py` — items 1 & 5 tests.
- `backend/tests/integration/test_pw_training.py` — item 3 tests.
- `backend/tests/integration/test_pw_engine_scale.py` — item 3. The per-chunk training pin.
- `backend/tests/integration/test_pw_retention.py` — item 2 tests and the enum-drift guard.
- `backend/tests/integration/test_pw_collection.py` — item 4 collector test.
- `backend/tests/integration/test_property_watches_api.py` — items 4 & 6 tests and the
  `PROPERTY_OPTIONS_STATEMENTS` pin.
- `backend/tests/unit/test_pw_scope.py` — item 4 resolver tests.

Docs:

- `docs/PHASE-G-PROPERTY-WATCH.md` — `[BUILT — PW-3c]` markers and the sentences these rules change.
- `docs/tasks/PW-3c.report.md` — this file.

## What I did

**Item 1 — a retired value's open incident.** The resolve runs in the **engine's next run**, not in
the collector's retirement write, because the engine already holds every current-version value and
every detector state in memory: `_resolve_retired` reads `value.active` off values it loaded anyway
and adds no query per value (AGENTS.md rule 9). The retirement tick's own bucket is still judged
(the collector sets `tracked_until` to the newest bucket), so the resolve runs **after**
`_evaluate_pending`, and an incident opened by this run's own bucket is ended too, never stranded.
It uses the shared `_retire_state`, so the alert is resolved exactly as the version-bump path
resolves it: actor `pw-engine`, reason `value retired; no longer evaluated`. Shadow mode (alerting
off) ends the incident and touches no alert row.

**Item 2 — retention never deletes a value with an active incident.** The `pw_value` delete and its
"anything still left" check both add `NOT EXISTS (SELECT 1 FROM pw_anomaly a WHERE a.pw_value_id =
v.id AND a.state IN ('OPEN', 'RECOVERING'))`, so such a row is skipped rather than deleted. A third
bounded statement counts the skipped rows into the new `blocked` field of `PwRetentionReport`, so a
non-zero count is visible. The two SQL literals are pinned to `BLOCKING_INCIDENT_STATES` by a guard
test, so the raw SQL cannot drift from the enum.

**Item 3 — stale baseline cells.** `PwBaselineTrainer` upserts only the cells it recomputed, so a
cell whose samples aged out kept its last statistics. `_rebuild_chunk` now calls
`delete_stale_baselines(value_ids, computed_at=now)` after the upsert: the rebuild marks every row it
writes with `computed_at`, so any other cell of those values (a stale `computed_at`, or `NULL`, a
row no rebuild ever wrote) is deleted. Detectors then read `INSUFFICIENT_DATA`. This is one bounded
statement per chunk; the per-chunk training cost goes from 5 to 6 statements, constant in the number
of values (pinned at 50 and 100 values in `test_pw_engine_scale.py`). See the open point on the
"unchanged" wording.

**Item 4 — scope staleness on the oldest walked row.** `resolve_scope` computed `now - own.synced_at`
on the watch's own group row, while `/scope` reported the oldest walked row. It now uses
`min(row.synced_at for row in walked rows)` and names that row's age in the reason. An interrupted
sync that leaves a descendant stale behind a fresh parent now fails the scope closed, and the
collector, the resolver and the API agree.

**Item 5 — a disabled watch's alerts.** When `PATCH /{watch_id}` turns `enabled` from true to false,
the route calls `PwAnomalyEngine.resolve_watch_incidents` (in-memory states plus two bulk reads,
one statement per lifecycle table, reason `watch disabled; no longer evaluated`) and then
`resolve_watch_alerts` with the principal's subject as the actor and the same reason, both in the
disable's own transaction. `resolve_watch_alerts` gained a `reason` parameter defaulting to the
delete reason, so the delete path is byte-for-byte the same. Enabling again starts fresh (the old
incident is not resurrected); a `PATCH` that leaves `enabled` alone resolves nothing.

**Item 6 — `property-options` statement count.** Pinned constant at 2 and 20 groups and at 10 and
200 sources (`PROPERTY_OPTIONS_STATEMENTS`). Measured: a group scope costs 4 (group existence
check, the walk, its sources, the instance row), a type scope 1 (the instance row). No production
change was needed; the pin is the deliverable.

**Item 7 — the docstring.** `dispatch_property_watch_collection` now says the plan is collect →
evaluate → train in one `PwInstancePipeline` call. Nothing else in `tasks.py` changed.

## Gates

- `ruff check app tests` — clean (`All checks passed!`).
- `mypy app` — clean (`Success: no issues found in 267 source files`).
- unit suite `pytest -m "not integration"` — 4423 passed, 10 skipped, 1499 deselected.
- whole integration suite `pytest -m integration tests/integration/ -ra` — 1501 passed, 2
  deselected, 12 warnings, exit 0 (whole `tests/integration/` in one session). Run after the last
  test edit; the only warnings are pre-existing `PydanticSerializerUnexpectedValue` ones in
  `test_phase3_api.py`.

## Statement counts

- `property-options` (item 6), with an explicit `instance_id`: group scope 4 statements at 2 and 20
  groups (and 10 and 200 sources); type scope 1 at 10 and 200 sources. Flat.
- Training (item 3): the due-values read plus five bounded statements per chunk. For one chunk (as
  the scale test runs it) that is 6 statements at both 50 and 100 values, up from 5; the sixth is
  the stale-cell delete. Constant in the number of values.
- Retention (item 2): 3 statements for the `pw_value` step (delete, remaining check, blocked count),
  whatever the row count.

## Guard mutations

Each new guard was broken once; the named test failed; the code was restored (verified byte-for-byte
against a backup).

1. Item 1: `_resolve_retired` filter inverted (`if value.active`) →
   `test_a_retired_values_incident_and_alert_resolve` and
   `test_a_retired_values_incident_resolves_in_shadow_mode` failed (2 failed).
2. Item 2: the delete's incident `NOT EXISTS` removed →
   `test_retention_keeps_a_value_whose_incident_is_still_active` (both states) and
   `test_the_value_delete_protects_exactly_the_active_incident_states` failed (3 failed).
3. Item 2: the delete's `'OPEN'` changed to `'CANDIDATE'` (SQL/enum drift) →
   the enum-pin test and the OPEN keep test failed (2 failed, 1 passed).
4. Item 3: the `delete_stale_baselines` call removed →
   `test_a_cell_the_rebuild_did_not_write_is_deleted` and
   `test_a_value_whose_readings_all_aged_out_loses_every_cell` failed (2 failed).
5. Item 4: `oldest` reverted to `own.synced_at` → the unit
   `test_the_oldest_walked_row_decides_staleness_not_the_named_group`, the API
   `test_a_stale_descendant_marks_the_scope_read_stale` and the collector
   `test_an_interrupted_sync_leaves_a_stale_descendant_and_blocks_the_scope` failed (1 + 2 failed).
6. Item 5: the disable branch gated off (`if False and ...`) →
   `test_disabling_a_watch_resolves_its_incident_and_alert_as_the_principal` and
   `test_enabling_a_watch_again_starts_fresh` failed (2 failed).
7. Item 5: the `reason` argument dropped from the `resolve_watch_alerts` call →
   `test_disabling_a_watch_resolves_its_incident_and_alert_as_the_principal` failed (1 failed).
8. Item 6: the route mutated to issue one `SELECT` per member inside the group branch →
   `test_property_options_costs_the_same_statements_at_every_size` failed (1 failed).

## Open points

- **Item 3, the "unchanged" statement count.** The task says "Pin the statement count per chunk as
  unchanged." I read "unchanged" as *constant in the number of values*, which is what the existing
  pin (`test_training_scales_with_chunks_not_values`) checks. The per-chunk loop body grew from 4 to
  5 bounded statements, so a one-chunk run goes from 5 to 6 (verified: the test now pins 6 at both 50
  and 100 values). I could not fold the stale-cell removal into the existing `upsert_baselines`
  statement: the upsert runs in `executemany` form precisely to stay under the driver's
  bind-parameter limit for tens of thousands of metric rows (the repository docstring says so), and a
  data-modifying CTE cannot be combined with an `executemany` batch. Writing the stale cells as
  not-reliable tombstones instead of deleting them needs a read of the existing cells first, which is
  also +1 statement, so the count rises either way. If "unchanged" was meant literally (5 must stay
  5), this is the one decision to revisit; the behaviour (bounded, constant, fail-closed) is right.
- **Item 1 is an eventual guarantee, not same-tick.** The resolve runs in the engine's next
  successful run. `evaluate` and `train` run in their own savepoints, so a failed evaluation leaves
  the collector's retirement committed and the incident open until the next run that succeeds. That
  is inherent to putting the resolve where the statement budget is (in-memory, no per-value query);
  the collector cannot resolve it without the engine's incident rows and alert plumbing.
- **Item 5 covers only this `PATCH`.** The route resolves incidents and alerts only on a true→false
  transition of `enabled`. A watch disabled any other way (a direct DB update, as
  `test_pw_engine.py::test_a_disabled_watch_keeps_its_incident_untouched` still does) keeps its
  incident; there is no other API route that disables a watch, so this is a deliberate boundary.
- **`resolve_watch_incidents` reads `active_anomaly_id`.** The engine sets and clears
  `active_anomaly_id` and `open_anomaly_id` together (the Open/RESOLVED/NORMAL branches of
  `_advance`), so a state with a live incident always has `active_anomaly_id` set. If a future change
  ever leaves `open_anomaly_id` set with `active_anomaly_id` null, the disable path would clear both
  and orphan the incident; not reachable today.
- **Not run against a real QRadar console.** Everything here is DB-level (retirement, retention,
  staleness, disable) and is exercised on the mock provider. No item depends on live QRadar
  semantics, so this is a scope note, not a risk.
- **No frontend change.** All seven items are backend; the frontend quality bar and its screenshots
  do not apply.
