# Task F-6a report — custom-rule catalog endpoint

Status: **done.** Nothing committed. Branch `agent/f6a`.

## 1. Files changed

- `backend/app/schemas/custom_rule.py`: `CatalogMetricOut`, `CatalogSubjectKindOut`, `CustomRuleCatalogOut`. A metric carries `metric`, `subject_kind`, `unit`, `is_ratio`, `measurement` (`MeasurementRule`: `EXACT_ONLY` or `LOWER_BOUND_PROVES_INCREASE`) and `note`. It does not carry `reads` or `bulk_loader`. A subject kind carries `subject_kind` and `column`.
- `backend/app/api/routes/custom_rules.py`: `GET /catalog` on the existing router, registered before `/{rule_id}`. `build_custom_rule_catalog` fills it from `CUSTOM_RULE_METRICS`, `_SUBJECT_COLUMNS`, `SourceClass`, `RULE_OPERATORS`, `alerting_mode`, and the settings. `PREVIEW_HOURS_MIN` / `PREVIEW_HOURS_MAX` (1 and 168) are the preview query bounds and the catalog fields. `enabled_count` is the existing one-statement `_enabled_count`.
- `backend/tests/unit/test_custom_rule_catalog_api.py`: drift guard for metrics, pairing, source classes, operators, switches and the published preview bounds.
- `backend/tests/integration/test_custom_rules_api.py`: viewer 200, enabled-count, the route not shadowed by `/{rule_id}`, and a check that the OpenAPI sweep walks `GET /catalog`.
- `docs/tasks/F-6a.report.md`: this report.

Not edited: `validation.py`, `catalog.py`, the router mount, migrations, frontend, `docs/PLANNER.md`, `AGENTS.md`. No migration.

## 2. What I did

`GET /api/v1/custom-rules/catalog` is a read on the custom-rules router, so the mount's `read:custom_rules` guard covers it. `read:*` satisfies that. The handler does not check `custom_rule:write`.

The body is whatever the enforcing code already says:

- Metrics follow `CUSTOM_RULE_METRICS` insertion order (catalog order). Table names and loader names stay in the catalog module.
- Subject kinds follow `CustomRuleSubjectKind`. The column is `_SUBJECT_COLUMNS`, the pairing `validate_rule_draft` uses. The task does not allow editing `validation.py`, so the route imports that private map instead of copying it. Each kind names one column today (`log_source_id`, `source_class`, `waf_endpoint_id`, `ips_entity_id`; `IPS_SOURCE` shares `log_source_id` with `LOG_SOURCE`). A pairing whose tuple is not length 1 raises `RuntimeError` rather than dropping a name.
- Source classes are every `SourceClass` value, in enum order. That is the membership test in `validate_rule_draft` (`source_class not in {member.value for member in SourceClass}`).
- Operators are `sorted(RULE_OPERATORS)`, so the wire order does not follow the process hash seed. The set is the validator's set. Sorted order is `EQ`, `GE`, `GT`, `LE`, `LT`.
- `evaluation_enabled` is `settings.custom_rules_enabled`. `alerting_mode` is `alerting_mode(settings.custom_rules_alerting_enabled)`, the same helper a rule response uses (`shadow` or `alerting`). `max_enabled` is `settings.custom_rules_max_enabled`. `enabled_count` is one `COUNT` of enabled rules. `preview_hours_min` and `preview_hours_max` are the constants on both preview `Query(ge=..., le=...)` bounds.

401 and 403 are the existing OpenAPI sweep in `test_custom_rules_api.py` (`test_every_custom_rule_route_without_a_token_is_401` and `test_every_custom_rule_route_without_read_is_403`). Both walk `_operations`, which lists every `/api/v1/custom-rules` path in the live OpenAPI document. `test_catalog_is_on_the_openapi_guard_walk` asserts `("GET", "/api/v1/custom-rules/catalog")` is on that walk. There is no hand-written 401 or 403 case for this route. Both sweep tests passed in the integration run below, so the new path was requested and answered 401 without a token and 403 without `read:custom_rules`.

**Break-it** (code restored after each). Each new test failed once:

- `test_catalog_metrics_match_the_catalog_and_hide_internals`: note forced to `"drift"`. Failed `assert 'drift' == 'exact only …'`.
- `test_catalog_subject_kinds_match_the_validator_pairing`: every kind reported `source_class`. Failed `assert ('log_source_id',) == ('source_class',)`.
- `test_catalog_source_classes_are_what_validation_accepts`: the list was only `LINUX`. Failed `assert ['LINUX'] == ['WAF', 'IPS', …]`.
- `test_catalog_operators_are_the_validator_set`: the list was only `GT`. Failed `assert ['GT'] == ['EQ', 'GE', 'GT', 'LE', 'LT']`.
- `test_catalog_reports_switches_and_the_preview_bounds`: `evaluation_enabled` was negated. Failed `assert True is False`.
- `test_catalog_is_on_the_openapi_guard_walk`: the path was renamed `/catalogue`. Failed the membership assert; `/catalog` was absent from the walk.
- `test_viewer_catalog_returns_the_enforced_values`: same negation. Failed `assert True is False` against `settings.custom_rules_enabled`.
- `test_catalog_enabled_count_counts_enabled_rules_only`: the catalog call passed `enabled_count=0` instead of `_enabled_count`. Failed `assert 0 == 1`. A first attempt replaced the same text inside `_check_draft` and did not fail this test; the route itself was then changed.
- `test_catalog_is_not_shadowed_by_rule_id`: the catalog route was moved to after `/{rule_id}`. `GET /catalog` as a viewer returned 422 `uuid_parsing` on `rule_id` (`input` `catalog`).

## 3. Gates

All commands from `backend/`, `PYTHONPATH=.`, venv `/home/efe/Documents/QRadar-Dash/backend/.venv/bin/`. `ruff format` was not run.

Ruff (`ruff check app tests`):

```
All checks passed!
```

Exit 0.

Mypy (`mypy app`):

```
Success: no issues found in 240 source files
```

Exit 0.

Unit. The first invocation added a second `-q` on top of `addopts` (`-q` is already there). Exit 0, and that quiet level printed no count line. Progress glyphs were 3441 `.` and 9 `s`. Re-ran the task command without a second `-q`, with `--disable-warnings` so the count line is the summary:

```
3441 passed, 9 skipped, 1285 deselected, 5 warnings in 64.10s (0:01:04)
```

Exit 0.

Integration (`TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/qradar_obs_f6a`, `pytest -m integration tests/integration/test_custom_rules_api.py`). The first run (extra `-q`) exited 0 with 22 progress marks. Re-ran without the extra `-q`, with `--disable-warnings`:

```
22 passed, 5 warnings in 32.29s
```

Exit 0. That file holds the new catalog tests and the existing OpenAPI 401/403 sweep.

## 4. Open points

- Operator order on the wire is sorted. The validator's error sentence still lists `GT, GE, LT, LE, EQ`. The set is the same.
- `column` is one string because every current pairing tuple has length 1. A longer tuple fails the request with `RuntimeError` and fails `test_catalog_subject_kinds_match_the_validator_pairing`.
- `_SUBJECT_COLUMNS` is private. A public helper on `validation.py` would be the cleaner import. That file was outside this task.
- No statement-count test for the catalog read. The handler's only query is `_enabled_count`.
- No frontend. F-6 is the editor that should call this route.
