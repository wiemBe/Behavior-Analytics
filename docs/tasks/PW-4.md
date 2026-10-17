# Task PW-4 — Phase G alerting: the fingerprint, shadow mode, `AlertService` wiring, maintenance, resolution on version bump and delete

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 3 (the fingerprint
never carries the value text), 8 (fail closed: a maintenance or unknown bucket never resolves an
alert) and 9 (alerts open through `AlertService.apply_bulk`, a few statements per new alert, the
ruled exception of `docs/PHASE-D-IPS-BEHAVIOR.md` D.10; nothing per value otherwise), and the §4
test rules, including the integration rules of 2026-10-17. Then read
`docs/PHASE-G-PROPERTY-WATCH.md` (binding): **§G.10.1** in full, §G.8.0 "Lifecycle", §G.12,
§G.14.2, §G.15 ("Fingerprints", "Alerting", "Retention … deleting a watch removes every value and
resolves its alerts"). PW-3b (the engine and its incident lifecycle) is merged: build on it. Read
`docs/tasks/PW-3b.report.md` (where it left the alert hook and the `watch_version` resolution)
and PW-3a's open point 1 (maintenance).

The shape to follow is the IPS alerting: `IpsAnomalyEngine._apply_alert_actions`,
`_alert_input`, `_fingerprint` (`app/ips/engine.py`), `ips_anomaly_fingerprint`
(`app/alerts/fingerprint.py`) and `test_ips_alerting.py`.

## What this task builds

- `pw_anomaly_fingerprint(instance_id, watch_id, detector_type, value_key)` in
  `app/alerts/fingerprint.py` through `compute_fingerprint` with `source_type="pw_value"`,
  `condition="pw_anomaly"`, the detector type as `subtype`; `watch_version` is **not** in it; the
  value text only as a hash.
- **Shadow mode:** with `pw_alerting_enabled` false (or `pw_collection_enabled` false, or the
  family off), incidents are recorded and the engine opens, refreshes, resolves and enqueues
  **nothing**, and adds no statement. On: OPEN opens one alert, a still-anomalous incident
  refreshes it, RESOLVED resolves it (actor `pw-engine`, reason `condition recovered`),
  notifications enqueued per transition (D.10).
- **Context:** watch name, scope label, detector, the watch's severity, the value text, the
  reading with its measurement, the band, the bucket bounds.
- **Maintenance:** a `LOG_SOURCE` watch whose bucket is `in_maintenance` opens and refreshes
  nothing and does not resolve; a group watch only when every resolved member is (PW-2b stores
  the flag); a type watch has none in v1.
- **Resolution on a `watch_version` bump** (actor `pw-engine`, reason `watch definition
  changed`) at the point PW-3b resolves the incidents, and **on `DELETE`** of a watch: its open
  alerts are resolved before the delete (the PW API's `DELETE`, `services/property_watches.py`;
  keep it a small separate function, PW-6 adds its 409 to the same route).

Out of scope: the F.1 kind (PW-6), the UI.

## Tests

- Unit: the fingerprint is stable across retirement and reactivation, never contains the value
  text, and differs across detector types and from the IPS, WAF, F.1 and search fingerprints.
- Integration (your own database, below): nothing opens while alerting is off, the family is off
  or the source is in maintenance (and shadow mode adds no statement); open, refresh, resolve with
  the fingerprint; two incidents of one (watch, detector, value) deduplicated into one alert; a
  version bump and a delete resolve the open alerts; a maintenance bucket does not resolve;
  statements per new alert fixed (the `test_opening_alerts_costs_a_fixed_number_of_statements_per_new_alert`
  shape); log capture of an incident and its alert holds no value text outside the alert context.
- Break each guard once and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite, and the **whole** integration suite
once on your database with the log kept (`-m integration tests/integration/ -ra`; read the skip
count):
`TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_pw4`.

## Allowed files

- `backend/app/alerts/fingerprint.py` (the new function), `backend/app/property_watch/engine.py`
  (the alert actions), `backend/app/services/pw_pipeline.py` (only if the alert service must be
  passed in)
- `backend/app/services/property_watches.py`, `backend/app/api/routes/property_watches.py` (the
  `DELETE` resolution only)
- new test files under `backend/tests/unit/` and `backend/tests/integration/`
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-4]` markers in §G.10.1
- `docs/tasks/PW-4.report.md` (new)

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/pw4/`, never `/tmp`.
