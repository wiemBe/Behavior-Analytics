STATUS: FINAL

# PW-4 report

## Files changed
- backend/app/alerts/fingerprint.py: `pw_anomaly_fingerprint(instance_id, watch_id, detector_type, value_key)`.
- backend/app/property_watch/engine.py: engine uses it; alert actions (open/refresh/resolve), rounds, scope label, context, maintenance hold, version-bump resolve, optional enqueuer.
- backend/app/services/pw_pipeline.py: optional `enqueuer`; a NotificationDispatcher is built only while `pw_alerting_enabled`, handed to the engine only when present.
- backend/app/services/property_watches.py: `resolve_watch_alerts` (small separate function).
- backend/app/api/routes/property_watches.py: DELETE calls it after PW-6's 409 check.
- docs/PHASE-G-PROPERTY-WATCH.md: `[BUILT — PW-4]` markers in G.10.1.
- New tests: backend/tests/unit/test_pw_fingerprint.py (11), backend/tests/integration/test_pw_alerting.py (22).

## Behaviour and decisions
- Fingerprint: source_type pw_value, condition pw_anomaly, detector as subtype; key = [instance, watch, sha256(value_key)]; no version, no row id. Old shape: PW-3b's `_fingerprint` is removed. Rows written with it (none in production) keep their stored column; alerts always derive the fingerprint from the row's fields, so they carry the new one (test pins this). Delete finds alerts through `context.watch_id`, not the column.
- Shadow: alerting = `pw_alerting_enabled and pw_collection_enabled`. Off adds no statement touching alert tables (test records SQL).
- Maintenance (decision): verdicts and incidents still run (PW-3a design: no verdict exit); a bucket in maintenance opens/refreshes no alert, and a RESOLVED decision is held (counters/watermark move, state does not) until a non-maintenance bucket. Otherwise the incident would resolve and strand an open alert. Version-bump resolution ignores maintenance. Group "every member"/type "none" is PW-2b's stored flag, read as is.
- Rounds: several buckets in one run are applied in rounds so open -> resolve -> open of one fingerprint stays in order.
- DELETE: actor is the principal, reason "the watch was deleted", enqueues notifications; runs after the 409 check; a refused delete rolls back.
- Notifications use the default routing policy built in the pipeline (tasks.py untouched).

## Gates (from backend/, PYTHONPATH=., main venv)
- ruff check app tests: clean. mypy app: 0 errors (267 files).
- Unit suite: 4393 passed, 10 skipped (route-auth allowlist), 0 failed. Log /home/efe/Documents/QRadar-Dash-work/pw4/unit.log
- New integration file alone: 22 passed; fingerprint unit file: 11 passed.
- Whole integration (-m integration tests/integration/ -ra, one session, qradar_obs_pw4): 1396 passed, 1 failed, 0 skipped. The one failure is the known base failure tests/integration/test_phase_a_api.py::TestDetectorCapabilities::test_every_named_detector_is_published_with_its_status (PW detectors in all_detectors()); untouched. Log /home/efe/Documents/QRadar-Dash-work/pw4/integ.log

## Statement counts
New alert: exactly 3 statements each (savepoint, insert, release) — 9 vs 3 alerts differ by 18; refresh tick equal at 3 and 9 alerts; plus one scope-label statement when an alert opens. Shadow: 0 alert statements.

## Guard mutations
Killed: alerting switch ignored; family switch ignored; maintenance not gating open/refresh; maintenance resolving; version bump not resolving alert; single round (no ordering); delete not resolving; pipeline not passing enqueuer; no resolve on recovery; raw value in fingerprint; watch id dropped from fingerprint.
Survived: (a) refresh without `is_anomalous` — equivalent mutant, the enclosing branch already requires it; (b) DELETE resolving before the 409 check — unobservable, the refused request rolls back; the test still pins that the alert stays open.

## Open points
1. Alerts opened while alerting was on are not resolved by the engine if the switch is later turned off (same as IPS). They stay open until the switch returns or the watch is deleted/bumped (delete resolves regardless of switch).
2. Retired value with an open incident (PW-3b open point) keeps its alert open; undecided.
3. A held RESOLVED in a long maintenance keeps the alert open for the whole window (fail closed); an incident that has no alert is held too.
4. DELETE's notification dispatcher uses the default policy only (as custom-rule delete).
5. Not run against a real QRadar/Celery; the pipeline test drives PwInstancePipeline directly with training stubbed (training would replace hand-written cells).
6. group/type maintenance semantics are PW-2b's flag; not re-tested here.
