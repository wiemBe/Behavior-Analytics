STATUS: FINAL

# PW-7c report

## Files changed

- `backend/app/schemas/property_watch.py` — added `value_key` and `components` to `PinRuleOut`.
- `backend/app/services/property_watches.py` — selected the joined value text in
  `pinning_rules_by_watch`, split its canonical tuple key, and carried both fields through the
  single-watch and pins responses without another statement.
- `backend/tests/integration/test_pw6b_api.py` — pinned both response shapes, one- and two-property
  components, hostile text returned unchanged, and the existing one-statement rule join.
- `frontend/src/lib/api.ts` — added the two backend fields to `PwPinRule`.
- `frontend/src/components/propertywatch/ThresholdRules.tsx` — rendered the value carried by each
  rule and removed the values-page lookup and impossible missing-value branch.
- `frontend/src/components/propertywatch/ThresholdRules.test.tsx` — covered direct rule values,
  component-pair display, hostile text, and the existing writer/reader/error/empty states.
- `frontend/src/components/propertywatch/WatchDefinition.tsx` — stated the formatted family cadence
  and five-minute bucket width while retaining the Search cadence link.
- `frontend/src/components/propertywatch/WatchDefinition.test.tsx` — covered 5 min, 1 h and 8 h
  cadence sentences.
- `frontend/src/components/propertywatch/WatchWorkspace.tsx` — removed obsolete rule-value lookup
  plumbing and passed the backend rules directly to the tab.
- `frontend/src/components/propertywatch/WatchWorkspace.test.tsx` — proved a rule value outside the
  values page is still displayed.
- `frontend/src/app/behavior/watches/[id]/page.tsx` — removed the 168-hour/200-value fallback read.
- `frontend/src/app/behavior/watches/[id]/page.test.tsx` — asserted the rule carries its own value and
  exactly one values-page fetch is issued.
- `docs/tasks/PW-7c.report.md` — this report.

`docs/tasks/PW-7c.md` was already untracked when the task began and was not edited.

## What I did

- `PinRuleOut` now returns `value_key` and `components` in both `threshold_rules` and
  `PinsOut.rules`. `pinning_rules_by_watch` still executes one joined `custom_rule`/`pw_value`
  statement; components use the same `split_tuple_key` representation as `PwValueRefOut`.
- The Threshold rules tab reads the rule object only. A rule whose value is absent from the current
  values page renders its stored text, two-property values render as `first × second`, and hostile
  text remains literal text. Loading/error, forbidden/unknown permission, empty, writer and reader
  behavior remains intact.
- The watch page no longer performs the wider `hours=168, limit=200` values read.
- The Definition tab says, for example, “Every 1 h (the PROPERTY_WATCH family's setting in Search
  cadence); buckets are five minutes wide.” The shared Search cadence duration formatter supplies
  5 min, 1 h and 8 h.
- The production screenshot stack used the deterministic fixture API and ports 3116/8116/9116.
  All fixture, frontend and Chrome processes were stopped by their tracked sessions; all three
  ports were verified free afterwards.

## Gates

- `frontend/: npm ci` — exit 0. Verbatim summary: `added 470 packages, and audited 471 packages in
  4s`; audit summary: `6 vulnerabilities (1 moderate, 5 high)`.
- `backend/: .venv/bin/ruff check app tests` — exit 0: `All checks passed!`
- `backend/: PYTHONPATH=. .venv/bin/mypy app` — exit 0:
  `Success: no issues found in 267 source files`
- `backend/: PYTHONPATH=. ENCRYPTION_KEY=… .venv/bin/pytest -q -m 'not integration'` — exit 0;
  4,430 passed, 10 skipped, 0 failed. Double quiet mode emitted no count line; its verbatim final
  progress line was `................................................                         [100%]`.
- `backend/: ENCRYPTION_KEY=… PYTHONPATH=. TEST_DATABASE_URL=… .venv/bin/pytest -q -m integration
  tests/integration/test_pw6b_api.py tests/integration/test_property_watches_api.py
  tests/integration/test_pw_api_gaps.py` — exit 0; 66 passed, 0 failed in one integration session.
  Verbatim final progress line:
  `..................................................................       [100%]`.
- `frontend/: npm run lint` — exit 0: `✔ No ESLint warnings or errors`
- `frontend/: npm run typecheck` — exit 0; verbatim command output: `tsc --noEmit` with no
  diagnostics.
- `frontend/: npx vitest run --no-cache` — exit 0. Verbatim summary:
  `Test Files  314 passed (314)` / `Tests  3491 passed (3491)`.
- `frontend/: NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8116/api/v1 npm run build` — exit 0:
  `✓ Compiled successfully in 10.7s` and `✓ Generating static pages (35/35)`.
- Restored focused checks after the break tests — backend PW-6b file: 10 passed, 0 failed;
  frontend four affected test files: `Test Files  4 passed (4)` / `Tests  52 passed (52)`.
- External fixture/capture syntax — `python -m py_compile fixture_api.py` and
  `node --check capture.mjs`: exit 0, no diagnostics.
- `git diff --check` — exit 0, no whitespace errors.

The pytest runs emitted only dependency deprecation warnings (Starlette/httpx, slowapi asyncio,
python-json-logger and pyasn1); no application warning or failure occurred.

## Break-it results

Each mutation failed the intended test, then was immediately restored. The restored focused suites
were rerun green.

- Returned a two-property value as one unsplit component: backend
  `test_the_single_read_lists_the_threshold_rules_and_the_list_does_not` failed its
  `components == ["alice", HOSTILE]` assertion.
- Serialized the rule name as `value_key`: backend
  `test_pins_success_lists_the_rules_that_pin_a_value` failed the PinsOut value assertion.
- Removed `<` from the joined backend value: backend
  `test_the_single_read_lists_the_threshold_rules_and_the_list_does_not` failed the exact hostile
  `value_key` assertion.
- Rendered raw `value_key` instead of components: frontend “lists each rule…” failed, receiving the
  tuple separator instead of `alice × DENY`.
- Removed `<` from frontend output: frontend “renders hostile value text as text” failed.
- Filtered rules against the values page: the workspace outside-page rule test failed because
  `Backup flood` was absent.
- Added the old wider values request: the page test failed with “expected propertyWatchValues to be
  called 1 times, but got 2 times”.
- Rendered raw cadence seconds: all three Definition cases failed — 300, 3600 and 28800 seconds did
  not render `Every 5 min`, `Every 1 h` and `Every 8 h`.

## Screenshots

All six required viewport PNGs and their supplemental `-full.png` counterparts were opened and
inspected. The viewport captures are the review targets below.

- `/home/efe/Documents/QRadar-Dash-work/pw7c/rules-reader-1440x900.png` — four rules in a wide
  three-column table; `legacy-svc` is present outside the values page, hostile markup is literal
  text, states are readable, and the reader permission sentence replaces the create action.
- `/home/efe/Documents/QRadar-Dash-work/pw7c/rules-reader-1280x800.png` — the same four-rule reader
  state remains balanced and fully visible at the intermediate width.
- `/home/efe/Documents/QRadar-Dash-work/pw7c/rules-reader-1024x768.png` — all rule/value/state columns
  remain contained and readable at the narrowest supported width; vertical scrolling is available.
- `/home/efe/Documents/QRadar-Dash-work/pw7c/definition-cadence-1440x900.png` — “How it is collected”
  shows Enabled, Version, Cadence and Alerting in one row; the 1 h sentence, Search cadence link and
  five-minute bucket width are visible.
- `/home/efe/Documents/QRadar-Dash-work/pw7c/definition-cadence-1280x800.png` — the collection grid
  remains four columns with the full cadence sentence visible and no clipping.
- `/home/efe/Documents/QRadar-Dash-work/pw7c/definition-cadence-1024x768.png` — the collection facts
  reflow to two rows; the full cadence sentence and link remain visible without horizontal scroll.

Supplemental full-page paths follow the same basenames with `-full.png`, for example
`/home/efe/Documents/QRadar-Dash-work/pw7c/rules-reader-1440x900-full.png` and
`/home/efe/Documents/QRadar-Dash-work/pw7c/definition-cadence-1024x768-full.png`.

Real-browser overflow measurements were equal at every viewport:

- Rules: `1440/1440`, `1280/1280`, `1009/1009` (1024 viewport minus its vertical scrollbar).
- Definition: `1425/1425`, `1265/1265`, `1009/1009` (vertical scrollbars present).

## Open points

- The production build and screenshots used the deterministic fixture API, not a deployed QRadar
  stack. Risk if left unverified: environment-specific reverse-proxy, authentication or deployment
  configuration could behave differently even though the real response shapes and application
  rendering are covered.
- `npm ci` reports six dependency audit findings (one moderate, five high), unchanged by this task.
  Risk if left unresolved: an affected dependency path could remain exposed; dependency remediation
  is outside PW-7c's allowed files.
- The Python suite reports third-party deprecation warnings. Risk if left unresolved: future library
  upgrades may turn those compatibility warnings into failures; no PW-7c behavior is implicated.
