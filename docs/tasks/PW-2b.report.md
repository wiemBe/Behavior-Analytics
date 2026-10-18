# PW-2b report

## 1. Files changed

- `backend/app/property_watch/fold.py`: records the distinct observed-value count before the
  bounded fold cut so admission can report truncation honestly.
- `backend/app/property_watch/scope.py`: adds pure source/type/group resolution, cycle-safe
  descendant walking, chunking, freshness and size failures, fingerprints, grace flags, and the
  resolved members needed for group maintenance semantics.
- `backend/app/property_watch/admission.py`: adds pure threshold/quota/ceiling admission, pin
  synchronization, tracking windows, reactivation, and retirement.
- `backend/app/collectors/pw_collector.py`: adds one bulk-planned collection tick per instance,
  shared search budgeting, fail-closed watermarking, admission, maintenance, provenance, and staged
  bulk persistence.
- `backend/app/repositories/property_watch.py`: adds constant-statement bulk reads and one bulk
  write per PW table.
- `backend/app/services/pw_pipeline.py`: adds the collection-first instance pipeline seam that
  PW-3b can extend with evaluation and training.
- `backend/app/services/pw_retention.py`: adds bounded metric, bucket, and retired-value retention,
  including an exact check before reporting that work remains.
- `backend/app/providers/factory.py`: passes `pw_max_sources_per_search` through both process-wide
  and per-instance mock and REST provider construction.
- `backend/app/workers/tasks.py`: adds the cadence-gated collection dispatch and retention tasks.
- `backend/app/workers/celery_app.py`: adds the 60-second collection dispatch beat and daily
  retention beat entries.
- `backend/tests/unit/test_pw_scope.py`: covers all scope kinds, descendants and cycles, stale,
  empty and too-large states, chunking, fingerprints, and grace windows.
- `backend/tests/unit/test_pw_admission.py`: covers thresholds, quotas, ceiling, pins, tracking
  periods, reactivation, and retirement boundaries.
- `backend/tests/unit/test_pw_collector.py`: covers the completed-prefix/watermark and
  `NOT_COLLECTED` fact contracts.
- `backend/tests/unit/test_pw_dispatch_gate.py`: covers beat registration, disabled/early dispatch
  gates, and the PW source cap on all four relevant factory paths.
- `backend/tests/integration/test_pw_collection.py`: covers mock-provider collection end to end,
  first-run seeding, failed and budget-spent searches, admission privacy, scope states and changes,
  maintenance, retirement, combinations, and status handling.
- `backend/tests/integration/test_pw_dispatch.py`: covers the operator dispatch path, cadence claim,
  family-off no-op, persistence, and recorded run counts.
- `backend/tests/integration/test_pw_retention.py`: covers aging order, all three per-run batch
  bounds, continuation, exact-final-batch completeness, and collection-off operation.
- `backend/tests/integration/test_pw_scale.py`: pins SQL statement counts across 1/10 watches,
  50/200 values, pins/combinations, and different bucket counts.
- `docs/PHASE-G-PROPERTY-WATCH.md`: adds exactly the PW-2b build markers in G.4, G.6, G.7.2,
  G.7.3, and G.14.2; no other section is changed.
- `docs/tasks/PW-2b.report.md`: this report.

## 2. What I did

I reviewed the inherited uncommitted implementation rather than accepting its prior green runs.
The collector resolves all enabled watches from bulk inventory reads, uses one
`PwSearchBudget` per instance, collects through the real mock-provider query/parser/fold path, and
advances each watermark only through the contiguous completed prefix. A failed search writes one
`NOT_COLLECTED` gap with unknown counts; a budget-spent search writes nothing and is retried. A
first run collects only the latest closed bucket. Buckets, admitted/pinned values, sparse metrics,
and watch status are staged across all watches and written once per table.

Admission persists only pinned, already tracked, or newly admitted values. The privacy integration
test seeds a recognizable below-threshold value and scans every public table to prove that text is
absent. Pins bypass admission, automatic values obey the threshold, per-bucket quota and active
ceiling, and the bucket retains the admission outcome counters. Retirement closes tracking windows;
retention deletes aged metrics and buckets before eligible retired values, in bounded batches.

Scope resolution handles source, type and group scopes; group descendants are walked with a visited
set, including the lab's two-group parent cycle. Missing/empty, stale and too-large scopes fail
closed without a bucket. Group membership fingerprints open the configured grace window. The
inventory is loaded in bulk, not per watch. Collection now also applies the binding maintenance
rule: a source follows its own window, a group is in maintenance only when every resolved member is,
and a type has no maintenance state in v1.

The inherited work had two real defects/gaps that I fixed:

1. Group watches always persisted `in_maintenance = false`; resolved members are now carried from
   the existing bulk inventory read and tested end to end with mixed and all-maintained members.
2. Retention's inherited test constrained only the bucket deletion. The new test places more than
   one batch in metrics, buckets, and retired values and proves that every target deletes only one
   configured batch. I also fixed the edge case where exactly the final allowed batch was deleted:
   the task now checks whether rows actually remain before reporting `incomplete`.

I also removed the inherited 94-line documentation expansion and left only completion updates in
the five sections the task permits. G.11, owned by PW-5, is unchanged. No migration, API, schema,
RBAC, seed, catalog, detection, alerting, or frontend file was touched.

### Statement-count pin

`tests/integration/test_pw_scale.py` runs every cross-product of 1 and 10 watches with 50 and 200
tracked values. The exact collection counts are **9 SQL statements for source scope** and **10 for
group scope**. The counts are also unchanged by the number of buckets. Group scope costs one extra
bulk groups read. Both are below the design ceiling of 40; evaluation and training remain PW-3b.

### Guard mutations

Every mutation below was restored immediately after the named test failed. A final source-pattern
audit confirmed the original expressions, all three `LIMIT :batch` clauses, `return all(...)` for
group maintenance, and the completed-prefix watermark are present. `providers/factory.py` also
matches the inherited `factory.bak` byte for byte.

- **Privacy / inherited survivor:** changed admission so every kept candidate was eligible,
  including the one-event secret. Killed by
  `test_a_value_that_never_reached_admission_is_in_no_table`; it failed on `pw_value`. This is the
  direct persisted-text defect represented by the inherited "persist all kept" survivor.
- **Retention / inherited survivor:** removed the batch `LIMIT` from metrics, buckets and values.
  Killed by `test_every_retention_table_obeys_the_per_run_batch_bound`; the mutation deleted
  7/7/14 rows instead of 3/3/3.
- **Factory cap / inherited errored mutation:** replaced the configured cap with 50 on the
  process-wide mock path; its test observed 50 instead of 9. Repeated on the process-wide REST path;
  its test observed 50 instead of 11. The per-instance mock and REST paths are pinned by the same
  test module.
- **Watermark / inherited second-run guard:** advanced to the last grouped outcome instead of the
  completed prefix. Killed by
  `test_the_watermark_advances_only_past_the_buckets_that_completed` (09:55 instead of 09:25 UTC).
- **Group maintenance:** changed `all` members to `any` member. Killed by
  `test_a_group_is_in_maintenance_only_when_every_resolved_member_is`.
- **Retention completion:** reported every exactly-full final batch as incomplete. Killed by
  `test_an_exact_final_batch_is_complete`.

## 3. Gates

All commands ran from `backend/` with `PYTHONPATH=.` and the main checkout's virtualenv.

- `/home/efe/Documents/QRadar-Dash/backend/.venv/bin/ruff check app tests` — **passed**, `All checks
  passed!`.
- `PYTHONPATH=. /home/efe/Documents/QRadar-Dash/backend/.venv/bin/mypy app` — **passed**, 0 errors
  in 262 source files.
- Whole unit suite:
  `PYTHONPATH=. ENCRYPTION_KEY=... /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -m
  "not integration" -ra` — **4,255 passed, 10 skipped, 1,345 deselected, 5 warnings** in 73.65 s.
  The skips are the existing route-auth allowlist cases: 7 at
  `tests/unit/test_route_auth_guard.py:240` (allowlisted open path) and 3 at line 250
  (allowlisted). Log: `/home/efe/Documents/QRadar-Dash-work/pw2b/unit-final.log`.
- Whole integration suite, one session on the assigned database:
  `PYTHONPATH=. ENCRYPTION_KEY=... TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw2b
  /home/efe/Documents/QRadar-Dash/backend/.venv/bin/pytest -m integration tests/integration/ -ra`
  — **1,345 passed, 0 skipped, 2 deselected, 11 warnings** in 992.94 s. There are no skipped
  integration tests to name. Log:
  `/home/efe/Documents/QRadar-Dash-work/pw2b/integ-final.log`.
- `git diff --check` — **passed**, no whitespace errors.
- Documentation marker audit — **passed**, exactly five `[BUILT — PW-2b]` markers at G.4, G.6,
  G.7.2, G.7.3 and G.14.2; no G.11 diff.
- Statement-count integration pin — **passed**, 9 statements for source and 10 for group at every
  required watch/value size.

## 4. Open points

- No real QRadar console was used. The integration path uses the mock provider but deliberately runs
  the production AQL builder, parser, split driver and fold. A console-specific Ariel behavior or
  performance difference could therefore still appear; PW-10 owns live validation and measurement.
- PW-2b stops after collection. Evaluation, baseline training and lifecycle execution are not in
  this pipeline yet; until PW-3b extends it, collected PW facts do not produce anomaly verdicts.
- The API/permission/audit surface is owned by the parallel PW-5 task and was not inspected or
  changed here. A mismatch there could prevent operators from configuring otherwise-correct
  collection, but changing it in this worktree would violate task ownership.
