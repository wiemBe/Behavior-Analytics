# PW-6 report

Status: built; whole-integration result in the "Gates" section.

## Files changed
- new `backend/alembic/versions/0027_custom_rule_pw_value.py`: `custom_rule.pw_value_id` (FK `pw_value.id`, `ON DELETE RESTRICT`, index `ix_custom_rule_pw_value_id`), drop + recreate `ck_custom_rule_subject_matches_kind` with `PW_VALUE`. Downgrade deletes `PW_VALUE` rules (and the `PW_VALUE` watermark) before restoring the 0024 CHECK.
- `backend/app/models/custom_rule.py`: column, FK, recreated `CUSTOM_RULE_SUBJECT_CHECK`.
- `backend/app/models/enums.py`: `CustomRuleSubjectKind.PW_VALUE`, `CustomRuleMetric.PW_VALUE_EVENTS`.
- `backend/app/custom_rules/catalog.py`: the `PW_VALUE_EVENTS` entry (events/bucket, `LOWER_BOUND_PROVES_INCREASE`, not ratio, loader `PW_VALUE`, the §G.10.2 note).
- `backend/app/custom_rules/loaders.py`: `load_pw_values`, pure `pw_value_facts`, `PwValueBucket`.
- `backend/app/custom_rules/engine.py`: `_family_collected` (PW branch; unknown kind now returns False, no longer the default True), `_retired`, `_label`, `_subject_key`, loader dispatch, `_KIND_FAMILY[PW_VALUE] = PROPERTY_WATCH` (sixth watermark), `_load_rules` also outer-joins `pw_value` and `property_watch` (still one statement).
- `backend/app/custom_rules/validation.py`, `backend/app/schemas/custom_rule.py`, `backend/app/api/routes/custom_rules.py`: `pw_value_id` accepted and returned; pin-only 422 (`_pw_value_reason`).
- `backend/app/services/property_watches.py`: `dependent_rules`, `removed_pin_keys`, `dependency_refusal` (appended section).
- `backend/app/api/routes/property_watches.py`: `_refuse_if_pinned_by_rules` and three call sites (DELETE, PATCH, PUT pins).
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-6]` markers in G.7.4 and G.10.2.
- Outside the allowed list, needed (see open points): `backend/app/custom_rules/evaluate.py` (one line registering the interpreter).
- Tests: new `tests/integration/test_migration_0027.py`, `tests/integration/test_custom_rules_pw_value.py`, `tests/unit/test_custom_rule_pw_value.py`; edited `tests/integration/test_migration_0026.py` (head pin made lineage-based, per PW-1 rule), `tests/unit/test_custom_rule_catalog.py` (10 -> 11 metrics, `pw_value_metric.events` legal read), `tests/unit/test_custom_rule_catalog_api.py` (the `_paired` helper learns `pw_value_id`).

## What was done
- Kind, metric, catalog entry, migration 0027 (additive, RESTRICT), model matched (no drift).
- Loader: one statement per tick for every PW_VALUE rule (bucket-driven outer join to `pw_value_metric`, joined to `pw_value` by id list). Row = the value's count under `EXACT` (pinned + bucket `pinned_measurement` EXACT) else the bucket's `values_measurement`. No row = measured zero ONLY for a pinned value, tracked at the bucket (`tracked_since`/`tracked_until`), whose bucket `pinned_measurement` is EXACT and whose parsed share reaches `pw_min_coverage` (0.90; a silent scope, total 0, counts as covered; unknown totals do not). Buckets of another `watch_version` than the value's never join. Everything else is absent (unmeasured).
- Engine: PW branch in `_family_collected` (`pw_collection_enabled`), `_retired` (not pinned / not active / missing => SUBJECT_RETIRED), `_label` ("<watch name>: <value text>"), `_subject_key`, loader dispatch, sixth watermark via the generic per-kind logic. The defaults were hardened: an unknown kind is now not collected (was `True`), retired (was "not retired"), and has no label (RuntimeError), so a new kind cannot be silently judged.
- Pin-only rule: `_pw_value_reason` (422 "...is not pinned; a rule can watch only a pinned value, so pin it on its property watch first"); both `pw_value.pinned` and the watch's `pinned_values` must hold the pin.
- PW API: DELETE, PATCH that changes scope/properties (version bump), PATCH that drops a pin, and PUT /pins that drops a pin used by a rule all return 409 `{"message", "rules": [{"id","name"}]}`; nothing is written or audited on a refusal.

## Gates (from /home/efe/Documents/QRadar-Dash-wt/pw6/backend)
- `ruff check app tests`: All checks passed.
- `mypy app`: Success, no issues found in 265 source files (0 errors).
- Unit suite (`-m "not integration"`): 4362 passed, 10 skipped (all pre-existing), 0 failed. Log: /home/efe/Documents/QRadar-Dash-work/pw6/unit.log
- Whole integration suite (`-m integration tests/integration/ -ra`, one session, own DB qradar_obs_pw6): 1395 passed, 2 deselected, 0 failed, 0 skipped, in 28m48s (the machine was shared with another full run). Log: /home/efe/Documents/QRadar-Dash-work/pw6/integ.log
- `alembic check`: run inside `test_migration_0027.py::test_0027_round_trip_carries_data_and_has_no_drift` (`command.check` after upgrade head): clean. Round trip 0026 -> 0027 -> 0026 -> head carries Phase F data.
- Loader statement count (`test_the_loader_is_one_statement_at_10_and_at_100_rules`): the whole PW_VALUE tick is 5 statements at 10 rules and 5 at 100 rules; exactly 1 of them touches `pw_value_metric` at both sizes.

## Guard mutations (each applied once, a test failed, file restored)
M1 zero without pinned+EXACT -> unit measured-zero tests fail. M2 coverage floor removed -> unit fails. M3 tracking window removed -> unit fails. M4 family branch removed -> guard test (PW_VALUE) fails. M5 unpinned value not retired -> unit fails. M6 label branch removed -> guard fails. M7 loader dispatch removed -> dispatch test fails. M8 retirement branch removed -> guard + engine integration tests fail. M9 DELETE not refused, M10 version-bump PATCH not refused, M11 PATCH unpin not refused, M12 PUT /pins unpin not refused -> `test_the_three_409s...` fails each. M13 pin-only rule removed, M14 watch pin list not consulted -> `test_a_rule_pins_a_pinned_value_only` fails. M15 migration ON DELETE CASCADE -> two 0027 tests fail. M16 model CASCADE -> ORM restrict test and the no-drift `alembic check` fail. M17 downgrade does not delete PW rules -> CheckViolation, round-trip test fails (that mutated run once left a stuck session that stalled the next test in the same process; the single-test rerun failed cleanly).

## Open points
1. `backend/app/custom_rules/evaluate.py` is outside the allowed list but had to change by one line: it raises at import when a catalog metric has no interpreter. `PW_VALUE_EVENTS` reuses `_entity_events` (the loader puts the value's measurement in `dimension_measurement`). If left out the app does not import.
2. Existing tests edited (extension only, nothing weakened): catalog count 10 -> 11 and the legal-reads set in `test_custom_rule_catalog.py`; the `_paired` helper in `test_custom_rule_catalog_api.py` learned `pw_value_id`.
3. Preview (`preview.py`, not in scope) does not know PW_VALUE: a PW_VALUE rule's preview loads nothing, so every bucket reads NOT_MEASURED and fired_count is 0. That is honest but not useful until preview.py gets a PW branch (later task). Nothing wrong is drawn.
4. A value pinned by `PUT /pins` has no `pw_value` row until the next PW collection tick creates it, so a rule on a fresh pin must wait one tick (the editor in PW-8 must say so). A pinned value with no row yields a 422 "does not exist".
5. `PUT /pins` "reports the dependent rules" only in the 409 body. The success response `PinsOut` is unchanged because `schemas/property_watch.py` is outside the allowed files and PW-5b edits that area. If the UI needs the list on success, add a field there.
6. Race: a rule created at the same instant a watch delete is validated is stopped by the database (`RESTRICT`), surfacing as a 500 instead of a 409. The watch row is locked FOR UPDATE, the rule create is not; narrow window, no data loss.
7. The measured-zero rule adds the coverage floor (`pw_min_coverage`) and the tracking window on top of the task text, from design §G.10.2/§G.5/§G.8.0 (fail closed). If the orchestrator wants only "pinned + EXACT", remove `_pw_coverage_ok`/`_pw_tracked_at` in `loaders.pw_value_facts`.
8. Subject label persists the value text in `custom_rule_breach.subject_label` and so in the alert context, as the design (G.10) states for IPS-like exposure; pinned values already persist in `pw_value`.
9. Maintenance: a PW rule honours only the bucket's stored `in_maintenance` flag (no live source window, since the watch can span many sources). A group/type watch is never "live" paused.
10. PW-3b/PW-5b merge: my PW API edits are the helper `_refuse_if_pinned_by_rules` plus three one-call insertions (DELETE, PATCH before the mutation, PUT pins before the diff) and an appended section in `services/property_watches.py`.
