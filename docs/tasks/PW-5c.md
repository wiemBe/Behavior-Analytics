# Task PW-5c — Phase G API gaps found in the PW-5 and PW-7 reviews

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 3 and D-22 (value
text is shown to `read:*` callers, never logged), 4 (the generated AQL is shown read-only; nothing
from the browser runs), 5, 9 (**bulk reads**: a read route costs a fixed number of statements
whatever the number of values or watches), and the §4 test rules, including the integration rules
of 2026-10-17. Then read `docs/PHASE-G-PROPERTY-WATCH.md` §G.8.0 "Where incidents surface",
§G.11, §G.13.3 (Definition tab: the generated AQL), and `docs/tasks/PW-5.report.md` and
`docs/tasks/PW-7.report.md` §6 (the open points this task closes).

## What this task builds

1. **`GET /anomalies/{id}` for a `PW_VALUE` row.** Today it answers 404 for a PW incident, so the
   UI's investigation view (PW-7, `frontend/src/app/anomalies/[id]/`) has no backend. Serve the
   detail in the existing `AnomalyDetailOut` shape with the PW subject (watch, value text,
   components, detector, state, the lifecycle transitions from `pw_anomaly_transition`, the
   reading with its measurement and band), in the way the IPS and WAF kinds are served. Read
   PW-7's fixture (`/home/efe/Documents/QRadar-Dash-work/pw7/fixture_api.py`) for the shape the
   UI assumed; where it differs from what the backend can honestly serve, the backend wins and the
   report lists the differences for the UI.
2. **`value_id` in `PwValueRefOut`**, so the UI links to the watch page with the value selected
   without looking the value up among the first 200.
3. **The anomaly histogram counts PW incidents** (`app/repositories/anomaly_histogram.py`), like
   the other kinds, with the PW kind in its filter.
4. **A read-only view of the generated AQL** for a watch: the stored watch's current AQL as the
   preview's generator produces it (no Ariel call), and the last bucket's `query_provenance`,
   in `GET /property-watches/{id}` or a `GET /{id}/aql` route under the mount guard
   (`read:property_watches`). Text only; nothing is accepted.
5. **`list_values`** joins the newest bucket's metric rows without a watch filter (PW-5 open
   point): add the watch filter so the read is bounded by the watch, and pin its statement count
   at 50 and 200 values and at 1 and 10 watches.

## Tests

- Integration (your own database, below): the PW detail with its lifecycle; 404 for an unknown
  id; the permission matrix on the new route(s); `value_id` present; the histogram counts a seeded
  PW incident and filters by kind; the AQL view returns text and accepts nothing; the
  `list_values` statement count is constant.
- Privacy: log capture of the detail and the AQL view holds no value text.
- Break each guard once and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, and the **whole** integration suite
once on your database with the log kept (`-m integration tests/integration/ -ra`; read the skip
count): `TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw5c`.

## Allowed files

- `backend/app/api/routes/anomalies.py`, `backend/app/repositories/anomaly.py`,
  `backend/app/repositories/anomaly_histogram.py`, `backend/app/schemas/anomaly.py`
- `backend/app/api/routes/property_watches.py`, `backend/app/schemas/property_watch.py`,
  `backend/app/services/property_watches.py` (items 4 and 5 only, in small separate functions:
  PW-5b and PW-6 edit the same files)
- new test files under `backend/tests/unit/` and `backend/tests/integration/`
- `docs/tasks/PW-5c.report.md` (new)

Do not touch the frontend (a follow-up wires the UI to these). Do not commit. Scratch under
`/home/efe/Documents/QRadar-Dash-work/pw5c/`, never `/tmp`.
