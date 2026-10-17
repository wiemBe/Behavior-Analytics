# Task PW-5 — Phase G API: the router, permissions, audit, the catalog and option routes, reads, preview, pins

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 4 (the browser
never sends AQL; the preview runs only the stored watch), 5 (no secret, QRadar text or console
address in an error), 3 and the D-22 decision on value text, 9 (bulk reads), and the §4 test
rules, including the integration rules of 2026-10-17. Then read `docs/PHASE-G-PROPERTY-WATCH.md`
(binding): **§G.11** in full, §G.3 (the watch model and its validation), §G.6 "Preview", §G.7.2
(audit holds the count and a SHA-256 digest of the pinned list, never the values), §G.8.0 "Where
incidents surface", §G.12, §G.14 (the enable caps that make a `PATCH` 409). PW-1, PW-2a and PW-3a
are merged: build on their models, `reference.py`, the provider's `get_pw_keyed`, `aql.py`,
`fold.py`, `keyed_search.py`. Follow the `/custom-rules` router (`app/api/router.py`, the guard on
the mount) and `docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.11 for the 422 shape.

## What this task builds

- `read:property_watches` and `property_watch:write` in `app/security/rbac.py`; the detection
  engineer role gains `property_watch:write` additively in `app/services/seed.py` (no other role
  changes).
- The router mounted with its guard, and every route of §G.11 **except** the two below; the
  schemas; audit (`property_watch.create | .update | .delete | .enable | .disable | .preview`,
  `.enable`/`.disable` only when `enabled` is the only change); `expected_updated_at` and the 409
  on mismatch; the enable 409s of §G.14; `hours` 1–168, default 24.
- `property-options`: the many-types filter added to `PropertyCatalogService` (the cached
  service; it takes one `source_type_id` today).
- The preview: one real bounded run of the **stored** watch over the last closed hour (grouped +
  totals, exactly two searches) through `get_pw_keyed`, `aql.py` and the fold; persists nothing,
  admits nothing; the tighter search rate limiter; audited with counts only; returns the rows,
  coverage, unparsed remainder, marked `PREVIEW`, and the generated AQL read-only.
- `PUT /{id}/pins` with `PW_PINNED_VALUE_PATTERN`.
- The `PW_VALUE` subject kind in `/anomalies` (`app/schemas/anomaly.py:40-51`, the
  `anomaly_type` union at `app/api/routes/anomalies.py:149`) reading `pw_anomaly`, value text
  shown to `read:*` callers (D-22).

**Deferred, on purpose:**
- `GET /property-watches/{id}/scope` and the resolved member counts in `scope-options` need the
  pure scope resolver that the parallel task PW-2b is building; they come in **PW-5b** after
  PW-2b merges. `scope-options` in this task returns ids, names, a type's synced-source count
  and a group's path, without the resolved member count.
- `DELETE` deletes the watch with every value (cascade). Resolving its open alerts is added by
  PW-4 (alerting does not exist yet) and the 409 while an F.1 rule pins a value by PW-6 (the
  column does not exist yet). Do not add placeholders for them; say in the report where they go.

## Tests

- Integration (your own database, below): the permission matrix (401/403/404/409/422) on every
  route; a test that every route on the PW router carries the mount guard; 422 returns every
  reason at once; the version 409; the enable 409s; DELETE cascades every value.
- Privacy: log capture and audit rows of a create, an update with pins and a preview contain no
  pinned or seeded value text; the audit snapshot holds the count and the digest.
- The preview sends exactly two searches (count them on the mock provider), persists nothing, is
  audited with counts only and is rate-limited.
- `/anomalies` lists a seeded `pw_anomaly` row as `PW_VALUE` with its value text for a `read:*`
  caller.
- Unit: schema validation (references, pins, hours bounds), the many-types catalog filter.
- Break each guard once and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, and the **whole** integration suite
once on your database with the log kept (`-m integration tests/integration/ -ra`; read the skip
count):
`TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw5`.

## Allowed files

- new: `backend/app/api/routes/property_watches.py`, `backend/app/schemas/property_watch.py`,
  `backend/app/services/property_watches.py` (or `property_watch_api.py`)
- `backend/app/api/router.py`, `backend/app/security/rbac.py`, `backend/app/services/seed.py`,
  `backend/app/services/property_catalog.py`, `backend/app/schemas/anomaly.py`,
  `backend/app/api/routes/anomalies.py`, `backend/app/repositories/anomaly.py` (the `PW_VALUE`
  read only)
- new test files under `backend/tests/unit/` and `backend/tests/integration/`; the route-guard
  and permission-matrix tests that enumerate every route, if they need the new routes listed
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-5]` markers in §G.11
- `docs/tasks/PW-5.report.md` (new)

A parallel task (PW-2b) owns `app/property_watch/scope.py`, `admission.py`, the collector, the
pipeline, the retention service, `app/repositories/property_watch.py`, `app/workers/` and
`app/providers/factory.py`: do not touch them.

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/pw5/`, never `/tmp`.
