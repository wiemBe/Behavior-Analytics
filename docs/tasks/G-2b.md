# Task G-2b — `GET /geo/countries`: no measurement on an unmeasured range, and coverage over every instance

Read `AGENTS.md` at the repository root first and follow it exactly. These rules matter most here:
- **rule 8: fail closed.** A range with no measurement must not read as an exact zero, and a
  missing instance must not read as full coverage;
- §4: never weaken a test; stop and report a conflicting test.

G-2 (merged) built the geo collection and `GET /geo/countries` in `backend/app/api/routes/geo.py`
and `backend/app/schemas/geo.py`. Read [`G-2.md`](G-2.md) §7 and
[`G-2.report.md`](G-2.report.md) first. The orchestrator's review found two fail-open answers.

## Defect 1: a `NOT_COLLECTED` range carries a measurement

Today, when no row is in range, the response is `status: NOT_COLLECTED` but also
`measurement: "EXACT"` and `remainders: {UNRESOLVED: 0, OVER_CAP: 0, UNPARSEABLE: 0}`. Those are
an exactness claim and three zeros for something that was never measured (AGENTS.md §3a: no zero
where the measurement is missing).

**Fix:**
- `GeoCountriesOut.measurement` becomes `GeoMeasurement | None`, and `remainders` becomes
  `GeoRemaindersOut | None`.
- Both are `None` exactly when `status` is `NOT_COLLECTED`. In every other status they are set as
  today (a remainder kind with no row in a measured range stays a true `0`).
- `countries` stays an empty list for `NOT_COLLECTED`.
- Update each field's comment so the reason is stated in the file's voice.

## Defect 2: `expected_buckets` ignores an instance that has no row at all

Today `expected = expected_per_instance * count(distinct instance_id among rows in range)`. With
two enabled instances where one has no geo row in range (its collection failed every time, or it
was never collected), the expected count covers only the other one, so the response can read `OK`
with half the estate missing.

**Fix:**
- The expected count multiplies by the number of instances that **should** have been collected:
  the enabled `QRadarInstance` rows. Look at how the collector's task enumerates instances
  (`_for_each_instance` in `app/workers/tasks.py`) and use the same predicate, so the API and the
  collector agree on "an instance that is collected". Say in the report which predicate you used.
- Keep the route at **two statements**. Fold the instance count into the existing coverage
  statement (a scalar subquery is fine). Do not add a third query.
- With no rows in range the status stays `NOT_COLLECTED` regardless of the instance count.

## Tests

Extend `backend/tests/integration/test_geo_api.py` (add tests; do not weaken or delete any):
- `NOT_COLLECTED` returns `measurement: null` and `remainders: null`, and an empty `countries`;
- a measured range still returns a `measurement` and a `remainders` object, including a true `0`
  for a remainder kind that had no row;
- two enabled instances, rows for only one of them across the whole range: `expected_buckets` is
  twice the per-instance count and the status is `PARTIAL`, not `OK`;
- a disabled instance (by the predicate you used) does not raise `expected_buckets`;
- the statement count of the route is still two (use the repository's statement-recording helper,
  as the G-2 tests or `test_waf_scale.py` do).

If an existing test asserts `measurement == "EXACT"` or zero remainders on a `NOT_COLLECTED`
response, that test pinned the defect: change only that assertion, and list it in the report.
Any other conflicting test: stop and report.

Break the code on purpose for each of the two fixes and confirm a test fails; list them.

## Allowed files

- `backend/app/api/routes/geo.py`
- `backend/app/schemas/geo.py`
- `backend/tests/integration/test_geo_api.py`
- `docs/tasks/G-2b.report.md` (new)

`frontend/src/lib/api.ts` has no geo types yet, so nothing changes there.

## Gates

From `backend/`, with `PYTHONPATH=.` and the `ENCRYPTION_KEY` from AGENTS.md §5:
- `.venv/bin/ruff check app tests`;
- `.venv/bin/mypy app`: no new error over the baseline of 31 errors in 18 files;
- `.venv/bin/pytest -m "not integration"`: all green;
- `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_g2b
  .venv/bin/pytest -m integration tests/integration/test_geo_api.py tests/integration/test_geo_collection.py`.
  Use only `qradar_obs_g2b`.

`pyproject.toml` already passes `-q`. Copy every summary line verbatim into the report.

Write `docs/tasks/G-2b.report.md` in the AGENTS.md §6 format. Do not commit.
