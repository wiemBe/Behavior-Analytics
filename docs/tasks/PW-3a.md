# Task PW-3a — Phase G detection, the pure half: baselines, the five detectors, capabilities, history

Read `AGENTS.md` at the repository root first and follow it exactly: §2 rules 6 (no `PW_` member
in `AnomalyType`, `WafAnomalyType` or `IpsAnomalyType`) and 8 (fail closed; **a detector never
grants itself lower-bound reasoning**: its verdict scope comes from its spec in
`app/anomaly/capabilities.py`), and the §4 test rules. Then read `docs/PHASE-G-PROPERTY-WATCH.md`
(binding, with D-21, D-22 and `docs/tasks/PW-0.report.md`). PW-1 is merged: build on
`backend/app/property_watch/facts.py` (the reading contract and the measured-zero rule), the PW
enums and the `pw_*` settings; do not redefine them.

## What this task builds (design sections)

- **§G.8.0:** `BehaviorDomain.PW_VALUE`; `PW_DETECTORS: dict[PwAnomalyType, DetectorSpec]`,
  `PW_BASELINE_METRICS` and the `PW_VALUE` branch of `detector_status` in
  `app/anomaly/capabilities.py`, enabling setting `pw_collection_enabled`. The verdict arithmetic
  is **reused, not copied** (`WafMeasurementRequirement`, `verdict_scope`, `narrowest`,
  `ScopeDecision` from `app/waf/measurement.py`), exactly as the IPS specs do. The PW inputs map
  `PwBucketMeasurement` to the shared measurement the way `app/ips/inputs.py:76-79` does.
- **§G.8.0 "Baselines":** the pure `PW_VALUE_EVENTS_METRIC` builder: weekday × hour cells, median,
  MAD, p05/p95, `baseline_min_samples`, and the exclusion tally (`LOWER_BOUND`/unknown,
  `low_coverage`, `scope_changed`, maintenance, another `watch_version`, inside a known incident
  span), in the shape of `app/ips/baseline.py`. Pure: it takes readings and returns cells and the
  tally; loading and storing them is PW-3b.
- **§G.8.2:** `PW_VALUE_SPIKE`, `PW_VALUE_DROP`, `PW_VALUE_DISAPPEARED` with every guard, the
  fail-closed exits **in the order the design gives**, and the measurement requirements of its
  table (spike `LOWER_BOUND_PROVES_INCREASE`; drop and disappearance `EXACT`, coverage
  `LOWER_BOUND_PROVES_INCREASE`). A `LOWER_BOUND` bucket proves only the spike.
- **§G.8.3:** `PW_NEW_VALUE` with the pure `verify_value_history` (the
  `verify_endpoint_history` shape), the learning period, the exits in order, the flood exit, and
  pinned values never new.
- **§G.9.3:** `PW_NEW_COMBINATION`, exactly as written (exclusive classification against
  `PW_NEW_VALUE`, a component known only through a lower-bound bucket, a component seen only
  below the admission threshold, a one-property watch).
- §G.8.1 (distribution, no verdict) and §G.8.4 (F.1 thresholds) are **not** this task.

Out of scope: bulk loaders, the lifecycle, the pipeline step, `watch_version` handling and cost
pins (PW-3b); fingerprints and alerting (PW-4); the F.1 extension (PW-6). If a setting the
design names is missing from `config.py`, do **not** add it: say so in the report.

## Tests (unit; design §G.15 "Detectors, one test per exit of §G.8" and "Capability truth")

- One test per exit of each detector, in order, including measured zero against unknown zero,
  coverage gating, the learning state and the `scope_changed` exit.
- The **monotonicity property test** for the spike over randomized lower-bound counts (the
  `test_monotonicity_property` shape of the WAF tests) and its converse: a lower bound never opens
  a drop or a disappearance.
- `PW_NEW_VALUE`: every history-completeness failure (missing bucket, truncated admission,
  version mismatch, `NOT_COLLECTED`), the flood exit, the established exit, pinned never new, the
  learning period. `PW_NEW_COMBINATION`: the four cases above.
- The baseline builder: each exclusion lands in its tally; a cell below `baseline_min_samples` is
  stored and never drives a verdict.
- Capability truth: every `PwAnomalyType` has a spec; `detector_status` is `NOT_ENABLED` while
  `pw_collection_enabled` is false and `UNSUPPORTED` for a spec with an unknown baseline metric;
  no `PW_` value in the three other anomaly enums.
- Break each guard once and confirm a test fails; restore.

## Gates (from `backend/`, the main checkout's venv, `PYTHONPATH=.`)

`ruff check app tests`, `mypy app` (0), the whole unit suite (`-m "not integration"`; report the
counts). No database is given: run no integration tests.

## Allowed files

- `backend/app/anomaly/capabilities.py` (the PW additions only)
- new modules under `backend/app/property_watch/` (for example `inputs.py`, `baseline.py`,
  `detectors.py`, `history.py`). **Do not edit** `facts.py` or `reference.py`, and do not create
  `aql.py`, `fold.py` or `keyed_search.py`: a parallel task (PW-2a) owns them.
- new test files under `backend/tests/unit/`
- `docs/PHASE-G-PROPERTY-WATCH.md`: `[BUILT — PW-3a]` markers and as-built notes in §G.8 and §G.9
  only (PW-2a edits §G.4–§G.6)
- `docs/tasks/PW-3a.report.md` (new)

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/pw3a/`, never `/tmp`.
