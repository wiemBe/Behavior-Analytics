# Task PW-5b — Phase G API, the scope half: `GET /property-watches/{id}/scope` and resolved member counts

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 5 (no secret,
QRadar text or console address in an error), 8 (fail closed: a stale or failed inventory is
said, never shown as a count) and **9 (bulk reads: never a query per watch, per group or per
source)**, and the §4 test rules, including the integration rules of 2026-10-17. Then read
`docs/PHASE-G-PROPERTY-WATCH.md` (binding): §G.4 (resolution, the three scope states, the
fingerprint, the grace window), §G.11 (the `/scope` and `scope-options` rows), §G.12. PW-2b (the
pure resolver `app/property_watch/scope.py` and the one bulk read of an instance's groups and
sources) and PW-5 (the router, `app/services/property_watches.py`, the schemas) are merged:
build on them. Read `docs/tasks/PW-2b.report.md` and `docs/tasks/PW-5.report.md`.

## What this task builds

- **One resolver.** PW-5's `services/property_watches.py` has its own minimal `resolve_groups`
  for the capacity check, the preview and the property picker, marked to be replaced by PW-2b's.
  Replace it with PW-2b's resolver and bulk read, so the API, the enable caps and the collector
  can never disagree about a scope. Delete the minimal one; its tests move to the shared
  resolver's behavior, they are not weakened (AGENTS.md §4).
- **`GET /property-watches/{id}/scope`** (`read:property_watches`, under the router's mount
  guard): the resolved member count, the groups walked (ids and names, the cycle-safe walk), the
  inventory age (the group rows' oldest `synced_at`, UTC), the scope state as PW-2b's resolver
  reports it (resolved, or `SCOPE_EMPTY`, `SCOPE_STALE`, `SCOPE_TOO_LARGE`) with its reason, the membership-change history from the
  watch's buckets (each fingerprint change: bucket start UTC, member count before and after, in
  the grace window or not), and up to 200 member names, sorted, with the total when it is larger.
  A stale inventory still returns what it has, marked stale, never a zero.
- **`scope-options`**: a group's resolved member count, and a type's synced-source count, through
  the same bulk read. A group whose resolution is stale or failed shows no count and says why.
- `GET /property-watches/{id}`'s "resolved scope summary", if PW-5 left it partial, from the same
  resolver.

## Tests

- Integration (your own database, below): `/scope` for a source, a type and a group scope;
  the two-group parent cycle (the lab's groups) terminates and lists each group once;
  `SCOPE_STALE` (a group `synced_at` older than `pw_scope_stale_after_hours`) is marked and
  carries no zero; `SCOPE_TOO_LARGE`; the 200-name cap with the total; the membership history
  from seeded buckets; the permission matrix (401/403/404) and the mount guard on the new route.
- **Statement-count pins**: `/scope` and `scope-options` cost the same at 2 and at 20 groups and
  at 10 and 200 sources; report the numbers.
- The enable cap and the preview give the same member count as `/scope` for the same watch.
- Break each guard once and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, and the **whole** integration suite
once on your database with the log kept (`-m integration tests/integration/ -ra`; read the skip
count):
`TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw5b`.

## Allowed files

- `backend/app/api/routes/property_watches.py`, `backend/app/schemas/property_watch.py`,
  `backend/app/services/property_watches.py`
- `backend/app/property_watch/scope.py` and `backend/app/repositories/property_watch.py` **only**
  to expose what the API needs (a read the collector already does); name each change in the
  report
- test files under `backend/tests/unit/` and `backend/tests/integration/` for the PW API and
  scope
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-5b]` markers in §G.11
- `docs/tasks/PW-5b.report.md` (new)

PW-3b may run in parallel and owns the engine, `services/pw_pipeline.py`, `anomaly/` and
`workers/`: do not touch them. Do not commit. Scratch under
`/home/efe/Documents/QRadar-Dash-work/pw5b/`, never `/tmp`.
