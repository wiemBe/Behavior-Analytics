# Task PW-6b — Phase G backend follow-ups: the F.1 preview for a PW value, the pin users on success, the delete/create race as 409, the family cadence on a watch

Read `AGENTS.md` at the repository root first and follow it exactly (§2 rules 8 and 9; the §4
test rules, including the integration rules of 2026-10-17). Then read
`docs/PHASE-G-PROPERTY-WATCH.md` §G.10.2 (the pin-only rule), §G.11, §G.13.5 (the widget's
`stale` row: "older than twice the family cadence") and `docs/tasks/PW-6.report.md` §Open points
3, 5, 6 and 9 (binding for this task). PW-6 is on this branch: build on it.

## Build

1. **F.1 preview for `PW_VALUE`** (`app/custom_rules/preview.py`, PW-6 open point 3). Today a
   PW_VALUE rule's preview loads nothing, so every bucket reads `NOT_MEASURED`. Add the kind to
   `replay`'s inputs (`pw_value_id`), `_subject_key`, `_load` (through `load_pw_values`, with
   `coverage_floor=settings.pw_min_coverage` exactly as `custom_rules/engine.py:399` passes it)
   and `_maintenance_source` (a PW value has no live source window: `None`; the bucket's stored
   `in_maintenance` flag is what `interpret` reads, PW-6 open point 9). Plumb `pw_value_id`
   through `POST /custom-rules/preview` (`app/api/routes/custom_rules.py:697`) and whatever
   request schema it uses. The preview's statement count stays what it is for the other kinds
   (one metric statement).
2. **`PinsOut.rules`** (PW-6 open point 5): on a successful `PUT /property-watches/{id}/pins`, the
   response lists the custom rules that pin a value of this watch after the change, as
   `[{"id", "name", "pw_value_id"}]`, read in one statement. The pins editor (PW-8) uses it to
   show which pins a rule depends on.
3. **The race as 409, both sides** (PW-6 open point 6):
   - A watch `DELETE`, a scope or property `PATCH`, or a `PUT /pins` that removes a pin, whose
     database write fails on the `RESTRICT` foreign key because a rule on one of its values was
     created after the PW-6 check: a **409** with the PW-6 body `{"message", "rules": [{"id",
     "name"}]}` (re-read the rules after the rollback to the savepoint), never a 500.
   - A rule create or `PATCH` whose `pw_value_id` row was deleted after `_pw_value_reason` passed
     (the insert fails on the foreign key): a **409** saying the property-watch value no longer
     exists and to reload, never a 500. Other `IntegrityError`s keep their current handling
     (`_flush_name` re-raises anything that is not the name constraint).
   - Pin each with an integration test that forces the interleaving deterministically (two
     sessions, or the check stubbed past, as you judge; say which and why).
4. **`cadence_seconds` on `PropertyWatchOut`** (from the PW-9 review): the `PROPERTY_WATCH` family
   cadence, from `SearchCadenceService.get(SearchFamily.PROPERTY_WATCH)` exactly as
   `collectors/pw_collector.py` reads it, read **once per request** on the list and the single
   read (not per watch). Pin the list route's statement count as constant across 1 and 20 watches.

## Tests

- Unit and integration for each item, every guard and exit path; break each guard once and
  confirm a test fails; restore.
- Preview: a PW rule over pinned-value data shows `EXACT` readings and the breaches the engine
  would open on the same rows (compare with the engine's verdict on the same data); a missing
  bucket is `NOT_MEASURED`; a measured zero only under PW-6's rule; a `LOWER_BOUND` bucket stays
  a lower bound.
- The existing preview tests for the other kinds pass unchanged.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, and the **whole** integration suite
once on your database with the log kept (`-m integration tests/integration/ -ra`; read the skip
count): `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw6b`.

## Allowed files

- `backend/app/custom_rules/preview.py`, `backend/app/api/routes/custom_rules.py` (the preview
  plumbing and the 409), the custom-rule request schema the preview route uses (the new field
  only)
- `backend/app/services/property_watches.py`, `backend/app/api/routes/property_watches.py`,
  `backend/app/schemas/property_watch.py` (`PinsOut.rules`, `cadence_seconds`, the 409s)
- new test files under `backend/tests/unit/` and `backend/tests/integration/`; additive edits to
  existing PW test files where a new field changes an exact-shape assertion (list each in the
  report)
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-6b]` markers; `docs/tasks/PW-6b.report.md` (new)

Parallel tasks edit `services/property_watches.py`, `routes/property_watches.py` and
`schemas/property_watch.py` too (PW-4: the DELETE alert resolution; PW-5b: scope; PW-5c:
`value_id` in `PwValueRefOut`). Keep your edits in small, separate functions and fields so the
merge stays mechanical; do not reformat or move existing code.

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/pw6b/`, never `/tmp`.
