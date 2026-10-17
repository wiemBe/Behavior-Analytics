# Task PW-1 — Phase G foundations: enums, models, the first migration, settings, the fact contract

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 3, 6, 7, 8 and 9,
and the §4 test rules (including the three added on 2026-10-17). Then read
`docs/PHASE-G-PROPERTY-WATCH.md` in full; it is binding, with the user's answers D-21 and D-22
and the lab answers of PW-0 (`docs/tasks/PW-0.report.md`). The sections this task builds:

- **§G.3** the watch model (the `property_watch` row, its `CHECK`s, the status vocabulary,
  `watch_version`), `PW_NORMALIZED_FIELD_ALLOWLIST` (v1: `username` only) and
  `PW_PINNED_VALUE_PATTERN` (a pin with a quote, a backslash or a control character is refused,
  §G.6 and Q8);
- **§G.7** the tables (§G.7.1), what is never stored, admission's columns (§G.7.3), and **§G.7.4
  the migration, PW-1's half only**: the new tables, the two hypertables, the `search_cadence` seed
  for `PROPERTY_WATCH`. **The `custom_rule` change (`pw_value_id` and the recreated `CHECK`) is
  PW-6's migration, not this one** (§G.16);
- **§G.14.3** every `pw_*` setting with its validator and default (`PW_COLLECTION_ENABLED` and
  `PW_ALERTING_ENABLED` default false);
- **§G.6 and §G.8.0** the pure fact dataclasses that PW-2 and PW-3 build against (the fold's
  output per bucket: per value or tuple the count, the unparsed remainder, coverage, the
  measurement, truncation; the measured-zero rule of §G.6), with no I/O;
- the new enums in `backend/app/models/enums.py`, following the `WafAnomalyType` rationale:
  `PwAnomalyType` and whatever §G.3/§G.8 name (scope kind, status, measurement if not reused).
  **`AnomalyType` and `SearchFamily` members already persisted must not change**; adding the
  `PROPERTY_WATCH` family value is additive.

Out of scope: providers, AQL, collection, detection, alerting, API, UI (PW-2 onwards).

## Tests

- Model and migration: the migration upgrades, downgrades and upgrades again, `alembic check`
  reports no drift, and the two tables are hypertables after upgrade (use `TEST_DATABASE_URL`;
  follow `tests/integration/test_migrations.py`, which builds `Config()` **without** the ini
  file). The scope `CHECK` refuses zero and two scope columns; the properties `CHECK` refuses 0 and
  3 entries; each refusal is a test.
- Settings: each validator's bounds, and both switches default false.
- `PW_PINNED_VALUE_PATTERN`: accepts a plain value, refuses a quote, a backslash, a control
  character, an empty string and an over-length one.
- The allowlist: `username` in, `category` and IP or numeric fields out.
- The fact dataclasses: the measured-zero rule as pure functions (a COMPLETED, untruncated result
  with an absent key is `EXACT` 0; any other end state is unmeasured, never 0); integer-valued
  floats accepted, anything else refused (PW-0 L6); NULL and `''` both counted as unparsed (L5).
- Break each guard once and confirm a test fails, then restore it.
- The new integration tests pass inside the whole integration run, not only alone (AGENTS.md §4).

## Gates

From `backend/`, with `PYTHONPATH=.` and the main checkout's venv: `ruff check app tests`,
`mypy app` (0), the unit suite, and the **whole** integration suite once on your database
(read the skip count; report it).

## Allowed files

- `backend/app/models/enums.py`, `backend/app/models/property_watch.py` (new),
  `backend/app/models/__init__.py`
- `backend/app/core/config.py` (the `pw_*` settings only)
- `backend/app/property_watch/` (new package: the pattern, the allowlist, the fact dataclasses)
- `backend/alembic/versions/0026_property_watch.py` (new; check the head first and take the next
  number)
- new test files under `backend/tests/unit/` and `backend/tests/integration/`
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT]` markers and as-built notes on the sections built
- `docs/tasks/PW-1.report.md` (new)

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/pw1/`, never `/tmp`.
