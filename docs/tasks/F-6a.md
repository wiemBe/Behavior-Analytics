# Task F-6a — the custom-rule catalog endpoint the rule editor needs

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/PHASE-F-CUSTOM-DETECTIONS.md` §F.3.2, §F.11 and §F.12 (its §F.16 answers override it), and
the F-3 router `backend/app/api/routes/custom_rules.py` with its schemas
`backend/app/schemas/custom_rule.py`.

## Why

The rule editor (F-6, next) must offer a metric picker "enumerated from the catalog, with its unit
and its honesty note" (§F.12), and it must say when evaluation or alerting is switched off before
the first rule exists. The catalog lives only in `app/custom_rules/catalog.py`; no route exposes
it, and the frontend must not copy it. This task adds one read route.

## Build

`GET /api/v1/custom-rules/catalog` on the existing custom-rules router (so the router's
`read:custom_rules` guard covers it; register it **before** `/{rule_id}`, whose UUID path
parameter would otherwise answer 422 for `catalog`). Response model in
`app/schemas/custom_rule.py`, in the file's voice:

- `metrics`: one entry per `CUSTOM_RULE_METRICS` entry, in catalog order: `metric`,
  `subject_kind`, `unit`, `is_ratio`, `measurement` (`EXACT_ONLY` |
  `LOWER_BOUND_PROVES_INCREASE`), `note`. Do **not** expose `reads` or `bulk_loader` (table and
  loader names are internals).
- `subject_kinds`: the `CustomRuleSubjectKind` values, each with the subject column it needs
  (`log_source_id`, `source_class`, `waf_endpoint_id`, `ips_entity_id`) — derived from the same
  pairing that validation enforces, not restated by hand.
- `source_classes`: the `SourceClass` values a `FLEET_CLASS` rule may name (whatever validation
  accepts).
- `operators`: the operator set validation accepts.
- `evaluation_enabled` (`custom_rules_enabled`), `alerting_mode` (the same `alerting_mode(...)`
  helper the rule responses use), `max_enabled`, `enabled_count` (one count statement),
  `preview_hours_min` = 1 and `preview_hours_max` = 168 (from the constants the preview route
  uses, not new literals).

Every value comes from the code that enforces it; a guard test pins that the response equals
the catalog and the validator's sets, so the two cannot drift.

## Tests

- Unit: the response builder against `CUSTOM_RULE_METRICS` and the validator's operator,
  class and pairing sets (drift guard).
- Integration, on your own DB: 200 for a reader (`viewer`), with the values above; 401 without a
  token and 403 without `read:custom_rules` (the existing OpenAPI-driven guard test must cover
  the new route without a hand-written entry; check that it does); `enabled_count` reflects an
  enabled rule; the route is not shadowed by `/{rule_id}`.
- Break-it each new test and say which failed.

## Gates (from `backend/`, `PYTHONPATH=.`, the main checkout's venv)

`ruff check app tests` clean; `mypy app` 0 errors; unit
`ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= pytest -m "not integration"`;
integration on the `TEST_DATABASE_URL` your prompt gives: `tests/integration/test_custom_rules_api.py`
and your new tests. Summary lines verbatim in the report.

## Allowed files

`backend/app/api/routes/custom_rules.py`, `backend/app/schemas/custom_rule.py`, new or extended
tests under `backend/tests/` for custom rules, `docs/tasks/F-6a.report.md` (new). No migration, no
frontend. Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/f6a/`, never `/tmp`.
