# PW-5 report: the Phase G property-watch API

## Files changed
No file changed in this session. The inherited work was reviewed, all gates run, and no defect
was found that needed a code change. The inherited files (now in the orchestrator's wip
snapshot 091e84f) are:
- `backend/app/api/routes/property_watches.py` (new): the router and every §G.11 route except `GET /{id}/scope`.
- `backend/app/schemas/property_watch.py` (new): request and response schemas.
- `backend/app/services/property_watches.py` (new): validation, snapshots, capacity (§G.14), bulk reads, preview.
- `backend/app/api/router.py`: mounts the router behind `requires(PERM_PROPERTY_WATCH_READ)`.
- `backend/app/security/rbac.py`: `read:property_watches`, `property_watch:write`.
- `backend/app/services/seed.py`: detection engineer role gains `property_watch:write` (only that role).
- `backend/app/services/property_catalog.py`: many-types filter (`catalog_for_types`) on the cached service.
- `backend/app/schemas/anomaly.py`, `backend/app/api/routes/anomalies.py`, `backend/app/repositories/anomaly.py`: `PW_VALUE` subject kind, `PwAnomalyType`, `property_watch_id` filter, `pw_value` object.
- `backend/tests/unit/test_route_auth_guard.py`: three `_BODIES` entries added, nothing else (purely additive diff: `POST /property-watches`, `PATCH /{watch_id}`, `PUT /{watch_id}/pins`; no existing check weakened or removed).
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT - PW-5]` marker on the §G.11 heading and one "As built by PW-5" note inside §G.11. No other section touched (grep finds the marker only on line 1157).
- tests: `backend/tests/integration/test_property_watches_api.py` (28 tests), `backend/tests/unit/test_property_watch_api.py`.
- `docs/tasks/PW-5.report.md` (this file).

## What I did (review of the inherited work)
Read the whole route file and the validation, snapshot, capacity, read and preview parts of the
service against §G.11, §G.3, §G.6, §G.7.2, §G.14 and the task. Found nothing wrong:
- Routes: catalog, scope-options, property-options, list, create (always disabled, 201), detail, patch, delete (204, cascade), buckets, values, series, preview, pins. Static paths are registered before `/{watch_id}`. `hours` is 1-168, default 24. Errors: 422 `{"message","reasons"}` with every reason, 409 for version conflict (`current_version`, `updated_by`), name clash, the enable caps (numbers named), and preview refusals.
- Guards: mount guard in `router.py`; each write handler (create, patch, delete, preview, pins) calls `_guard` for `property_watch:write`. A unit test checks every route on the router carries the mount guard.
- Audit: `.enable`/`.disable` only when `changed == {"enabled"}`; snapshots hold `pinned_count` and `pinned_digest` (`sha256:` plus hex of the sorted pins), never values; preview audit holds counts and states only; reads are not audited.
- Preview: `PREVIEW_SEARCHES = 2` budget (`PwSearchBudget`), totals then grouped, through `run_search` / `aql.py` / the fold; persists nothing; rate-limited with `rate_limit_search_execution`; returns generated AQL read-only; the body takes no AQL. A grouped result cut at the row limit is not split (it would be a third search): buckets read NOT_COLLECTED and the response says so. A failed search is unmeasured, never a zero, and no upstream text is returned.
- `/anomalies`: `PW_VALUE` rows come from `pw_anomaly`, with `pw_value` (value text, components) for any `read:*` caller (D-22); `log_source_id` is nullable for type- and group-scoped watches.
- Seed: only `detection_engineer` gains the permission; unit test checks no other role changes.
- Deferred, as the task says: `GET /{id}/scope` and resolved member counts in `scope-options` (PW-5b, needs PW-2b's resolver; `scope-options` returns ids, names, a type's synced-source count, a group's path). `DELETE` resolving open alerts goes in PW-4 (alerting). The 409 while an F.1 rule pins a value goes in PW-6 (the column does not exist). `PUT /pins` does not yet report dependent F.1 rules (PW-6). No placeholders were added.
- Mutation leftovers: the scratch backup `mut/property_watches.py.bak` was shared by the routes and services files (same basename) and matched the services file; the routes file therefore had no backup. I read the full routes file and re-ran all mutations (the script restores in `finally`); `git status` afterwards shows a clean tree against the snapshot, so nothing is left applied. `router.py` matches its backup.

## Pydantic serializer warning
`Expected enum ... field_name='source', input_value='EXPLICIT'` does not come from this task. It is emitted by
`tests/integration/test_phase3_api.py::TestCoverageWriteAuthorization::test_coverage_write_creates_the_mapping_and_records_the_actor`
(`app/schemas/coverage.py` `MappingSource`, a Phase 3 coverage-mapping response). None of the 28 PW tests
emits a serializer warning. I did not touch it (outside the allowed files). If left, it is only noise.

## Gates (from `backend/`, `PYTHONPATH=.`)
- `ruff check app tests`: All checks passed.
- `mypy app`: Success, no issues found in 259 source files (0 errors).
- Unit suite (`-m "not integration"`): 4225 passed, 10 skipped, 1336 deselected.
- Whole integration suite (`-m integration tests/integration/ -ra`, log `/home/efe/Documents/QRadar-Dash-work/pw5/integ3.log`): 1336 passed, 2 deselected, 12 warnings, 0 skipped, 0 failed (26m57s, the machine was busy with parallel tasks). PW file alone: 28 passed.
- An earlier whole run (`integ2.log`) was cut short without a summary because it was started as a child of a short shell; I reran it in the foreground manager and `integ3.log` is the result above.

## Guard mutations (script `/home/efe/Documents/QRadar-Dash-work/pw5/mutate.py`, log `mutations2.log`)
All 23 mutations were CAUGHT (a test fails), then restored: mount guard removed; write guard removed on create, patch, delete, preview, pins; patch version check; pins version check; enable capacity check; pins capacity recheck; pin pattern check; every-reason-at-once; preview budget 3; preview rate limit; hours upper bound; series max values; digest leaks pins; snapshot holds pins; version bump on pins; enable/disable audit action; watch created enabled; unknown value ids ignored; zero for an unmeasured preview total.

## Open points
- `GET /{id}/scope` and member counts: PW-5b. Until then a group's size in capacity checks comes from the API's own `resolve_groups` (synced inventory, descendants walk), not the collector's resolver; PW-5b must swap it, or the API and the collector could disagree on a group's size.
- PW-4 must add "resolve open alerts" to `DELETE`; until then deleting a watch removes its incidents by cascade without a resolution trail.
- PW-6 must add the 409 on `DELETE` and the dependent-rules report on `PUT /pins`; until then a watch pinned by an F.1 rule (none can exist yet) could be deleted.
- The anomaly histogram does not count PW incidents (its repository is outside this task).
- `list_values` joins the metric of the newest bucket without a watch filter on the metric subquery (joined by value id, so correct); at large scale it scans all values of that bucket. Not measured at scale.
- Not run against a real QRadar; the preview is tested on the mock provider (two searches counted).
