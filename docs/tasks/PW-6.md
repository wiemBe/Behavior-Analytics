# Task PW-6 — Phase G F.1 extension: the `PW_VALUE` subject kind, the second migration, the loader, the engine branches, the pin-only rule

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 7 (**migrations are
additive**: a new migration, never an edit of `0024` or `0026`; upgrade, downgrade, upgrade
cleanly; `alembic check` clean), 8 (fail closed) and 9 (one loader statement per tick, constant in
rule count), and the §4 test rules, including the integration rules of 2026-10-17. Then read
`docs/PHASE-G-PROPERTY-WATCH.md` (binding): **§G.10.2** in full, §G.7.4, §G.8.0 (the reading
table: a measured zero is proven only for a pinned value whose bucket's `pinned_measurement` is
`EXACT`), §G.12, §G.15 "F.1 extension", and `docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.4, §F.11,
§F.13, §F.14. PW-1, PW-2b and PW-5 are merged: build on them.

## What this task builds

- `CustomRuleSubjectKind.PW_VALUE` and `CustomRuleMetric.PW_VALUE_EVENTS` (additive), and the
  catalog entry in `app/custom_rules/catalog.py` modelled on the IPS `EVENTS` entry: unit
  `events/bucket`, measurement `LOWER_BOUND_PROVES_INCREASE`, not a ratio, loader `PW_VALUE`, the
  note of §G.10.2.
- **Migration `0027`** (revises `0026`): `custom_rule.pw_value_id` (FK to `pw_value.id`,
  **`ON DELETE RESTRICT`**, indexed) and the subject-pairing `CHECK` dropped and recreated with
  the new kind, in the pattern of `CUSTOM_RULE_SUBJECT_CHECK` (`app/models/custom_rule.py`). The
  model matches it. Its test in the `test_migration_0024.py` / `test_migration_0026.py` shape:
  upgrade, downgrade, upgrade, data carried, the recreated `CHECK` accepts and refuses the right
  pairings, `alembic check` clean. Fix the head pins of earlier migration tests only the way
  PW-1 did ("exactly one head, with `0026` in its lineage"), never by deleting a check.
- **The loader** in `app/custom_rules/loaders.py`: one statement per tick for every `PW_VALUE`
  rule; it reads the bucket row too and returns a **measured zero** only for a pinned value with
  no metric row in a bucket whose `pinned_measurement` is `EXACT`; anything else without a row
  is unmeasured; a `LOWER_BOUND` reading carries its measurement. `implemented_loaders()` covers
  the kind.
- **The engine branches** in `app/custom_rules/engine.py`: `_family_collected` for `PW_VALUE`
  (false while `pw_collection_enabled` is off or the family is off, never the default `True`),
  `_retired`, `_label`, and the sixth per-kind watermark. A guard test fails the build when any
  `CustomRuleSubjectKind` lacks a loader, a family branch, a retirement branch or a label.
- **The pin-only rule**, in the custom-rule API (`schemas/custom_rule.py`,
  `api/routes/custom_rules.py`, `custom_rules/validation.py`): `pw_value_id` accepted for the
  kind; a rule on a value that is not pinned is refused (422 reason naming why and that the
  analyst pins it first).
- **The PW API refusals** (`services/property_watches.py`, `api/routes/property_watches.py`):
  `DELETE` → 409 while an F.1 rule pins one of the watch's values, naming the rules; a `PATCH`
  that bumps `watch_version` → 409 likewise; `PUT /{id}/pins` → reports the dependent rules and
  refuses (409) to unpin a value a rule uses. Keep these in small, separate functions: PW-5b edits
  the same files in parallel.

Out of scope: the UI picker (PW-8), alerting (PW-4), the engine of PW-3b.

## Tests

- Unit: the catalog guard; `LOWER_BOUND` with `GT` opens and never resolves, with `LT` it is
  `INSUFFICIENT_DATA`; the measured-zero rule (pinned + `EXACT` only); an unknown subject kind
  fails the guard.
- Integration (your own database, below): the migration round trip; a `PW_VALUE` rule evaluated
  end to end on seeded buckets (measured zero, unmeasured, lower bound); the family gate;
  **the loader is one statement at 10 and at 100 rules** (report the number); the pin-only 422;
  the three 409s; `ON DELETE RESTRICT` holds at the database.
- Break each guard once and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, `alembic check` on your database
after `upgrade head`, and the **whole** integration suite once with the log kept
(`-m integration tests/integration/ -ra`; read the skip count):
`TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw6`.

## Allowed files

- new: `backend/alembic/versions/0027_custom_rule_pw_value.py`,
  `backend/tests/integration/test_migration_0027.py`
- `backend/app/models/enums.py` (the two members), `backend/app/models/custom_rule.py`
- `backend/app/custom_rules/catalog.py`, `loaders.py`, `engine.py`, `validation.py`
- `backend/app/schemas/custom_rule.py`, `backend/app/api/routes/custom_rules.py`
- `backend/app/services/property_watches.py`, `backend/app/api/routes/property_watches.py` (the
  three refusals only)
- earlier migration tests' head pins (the PW-1 rule above); new test files
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-6]` markers in §G.7.4 and §G.10.2
- `docs/tasks/PW-6.report.md` (new)

PW-3b (engine, pipeline, `anomaly/`, `workers/`) and PW-5b (the `/scope` route, the resolver
swap) run in parallel: do not touch their parts. Do not commit. Scratch under
`/home/efe/Documents/QRadar-Dash-work/pw6/`, never `/tmp`.
