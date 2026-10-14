# G-2b report: `GET /geo/countries` — no measurement on an unmeasured range, coverage over every instance

## 1. Files changed

- `backend/app/schemas/geo.py`: `GeoCountriesOut.measurement` and `.remainders` are now
  `GeoMeasurement | None` and `GeoRemaindersOut | None`, with comments saying they are `None`
  exactly while `status` is `NOT_COLLECTED`; the module docstring gains the same sentence.
- `backend/app/api/routes/geo.py`: the route answers `measurement: null` and
  `remainders: null` for a `NOT_COLLECTED` range; `expected_buckets` multiplies the settled
  per-instance count by the number of **enabled** `QRadarInstance` rows, folded into the
  existing coverage statement as a scalar subquery (still two statements).
- `backend/tests/integration/test_geo_api.py`: five new tests (listed below). No existing test
  was edited, weakened or deleted.

## 2. What I did

- **Defect 1.** `remainders` and `measurement` are `None` exactly when `verdict is
  GeoStatus.NOT_COLLECTED` (i.e. no row in range), and set exactly as before in every other
  status. `countries` stays `[]` for `NOT_COLLECTED`. A remainder kind with no row in a
  **measured** range is still a true `0` (`GeoRemaindersOut`'s field defaults).
- **Defect 2.** The predicate is `QRadarInstance.enabled.is_(True)` with no name filter — the
  same predicate `_for_each_instance` uses (`app/workers/tasks.py:181`,
  `select(QRadarInstance).where(QRadarInstance.enabled.is_(True))`, the `instance_name`
  branch unused by the collector's normal run). It is a scalar subquery in the existing
  coverage statement, so the route still issues exactly two statements: one aggregate, one
  coverage count that now returns `(settled buckets seen, enabled instances)`.
  `expected = expected_per_instance * enabled_instances`. With no rows in range the status
  stays `NOT_COLLECTED` regardless of the instance count; the coverage object still reports
  `expected_buckets > 0` when enabled instances exist, which is the fail-closed answer (the
  estate should have been collected and was not).
- **Tests added** (`tests/integration/test_geo_api.py`):
  - `TestContent::test_not_collected_carries_no_measurement_and_no_remainder_zeros` —
    `measurement` and `remainders` are `null`, `countries` is `[]`, status `NOT_COLLECTED`;
  - `TestContent::test_a_measured_range_reports_a_measurement_and_true_zero_remainders` — a
    measured range answers `measurement: "EXACT"` and a `remainders` object with a true `0`
    for `OVER_CAP` and `UNPARSEABLE`, which had no row;
  - `TestStatus::test_an_instance_with_no_row_at_all_still_raises_the_expected_buckets` — two
    enabled instances, full-hour rows for one only: `{measured: 12, expected: 24}` and
    `PARTIAL`, not `OK`;
  - `TestStatus::test_a_disabled_instance_does_not_raise_the_expected_buckets` — a second
    instance flipped to `enabled = False` (with full-hour rows, so the old distinct-instance
    predicate would double the count): `{measured: 24, expected: 12}` and `OK`;
  - `TestContent::test_two_statements_when_nothing_is_collected` — the route issues two
    statements on an empty range too (the existing
    `test_two_statements_whatever_the_data` still pins the full-data case, via
    `Client.statements` from `test_fleet_api.py`).
- **No existing test pinned the defect**, so none was changed. `test_nothing_collected_in_range_is_not_collected`
  asserts status/countries/coverage only and still passes (no instance is seeded there, so
  `expected_buckets` is `0` under either predicate).

### Break-it checks (each edit applied, the named test run, then the code restored)

All three were caught:
1. Defect 1 reverted (`measurement=EXACT`, `remainders` always the object) →
   `test_not_collected_carries_no_measurement_and_no_remainder_zeros` failed
   (`assert body["measurement"] is None`).
2. Defect 2 reverted (second coverage column back to `count(distinct instance_id)` among the
   rows in range) → `test_an_instance_with_no_row_at_all_still_raises_the_expected_buckets`
   failed (`expected 12 != 24`).
3. Predicate broken (scalar subquery counts every `QRadarInstance`, enabled or not) →
   `test_a_disabled_instance_does_not_raise_the_expected_buckets` failed (`expected 24 != 12`).

The final gates below ran on the restored tree.

## 3. Gates (summary lines copied verbatim)

From `backend/`, with `PYTHONPATH=.` and the `ENCRYPTION_KEY` from AGENTS.md §5:

- `.venv/bin/ruff check app tests`: `All checks passed!`
- `.venv/bin/mypy app`: `Found 31 errors in 18 files (checked 204 source files)` (baseline
  31 in 18; `mypy app | grep geo` prints nothing, so no error is in the files this task
  touches).
- `.venv/bin/pytest -m "not integration"`: `2847 passed, 5 skipped, 981 deselected, 2 warnings in 45.39s`
- `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_g2b .venv/bin/pytest -m integration tests/integration/test_geo_api.py tests/integration/test_geo_collection.py`:
  `55 passed, 7 warnings in 65.26s (0:01:05)` (35 collected in `test_geo_api.py`, all six new
  or touched tests among them).

## 4. Open points

1. **mypy counts 204 source files**, not the 202 the G-2 report saw. The error count is
   unchanged at 31 in 18 files and none is in `app/api/routes/geo.py` or
   `app/schemas/geo.py`; other merged tasks added files between the two baselines.
2. **A disabled instance's rows still count toward `measured_buckets`.** Only `expected`
   changed; the aggregate and the measured-bucket count still cover every instance with rows
   in range. The task did not ask to scope them, and scoping them would need the instance id
   in the aggregate statement. The disabled-instance test therefore asserts 24 measured
   buckets against 12 expected, which reads `OK`.
3. **`expected_buckets` is now estate-wide**, so a range with enabled instances and no rows
   reports e.g. `{measured: 0, expected: 12}` with `NOT_COLLECTED`. That is the fail-closed
   answer the fix intends, but a UI reading `coverage` without `status` will see a large
   expected number where before it saw `0`. `frontend/src/lib/api.ts` has no geo types yet,
   so nothing needed changing there (the task said so); whoever builds the geo UI must key
   the honesty states on `status` first.
4. **Not run:** the full integration suite and the alembic round-trip (this task changes no
   migration and names only the two geo integration files as gates); no frontend gate (no
   frontend file changes, and no screenshots — backend task). No state-changing git command
   was run, nothing was committed, and the `.venv`, `node_modules` and `qradar-mcp/` gitlink
   were not touched.
