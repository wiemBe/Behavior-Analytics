# PW-7 report: Phase G UI, the read side

## 1. Files changed

New:
- `frontend/src/app/behavior/watches/`: `page.tsx` (list), `layout.tsx`, `loading.tsx`, `error.tsx`, `watches.css`, `[id]/page.tsx` (watch page), `[id]/loading.tsx`, `[id]/error.tsx`, tests (`page.test.tsx`, `boundaries.test.tsx`, `[id]/page.test.tsx`).
- `frontend/src/components/propertywatch/`: `PropertyWatchSeriesChart.tsx` (the shared chart), `pwSeries.ts` (plot model, the gap rules), `pwChartOption.ts` (ECharts option, tooltip, coverage model), `pwFormat.ts` (labels, readings, coverage, status), `pwRange.ts` (windows, sort, selection ids, default selection, page size), `PwStatus.tsx`, `WatchWorkspace.tsx` (tabs, selection, series read), `WatchValuesTable.tsx`, `WatchHonesty.tsx`, `WatchDefinition.tsx`, `WatchIncidents.tsx`, `PwInvestigation.tsx`, `propertywatch.css`, `fixtures.ts`, and a test file for each logic part.
- `frontend/src/app/anomalies/pw.page.test.tsx`, `frontend/src/app/anomalies/[id]/pw.page.test.tsx`.

Edited:
- `frontend/src/lib/api.ts`: property-watch types and calls (list, one, catalog, scope, buckets, values, series); `SubjectKind` gains `PW_VALUE`; `AnomalySummary.pw_value`; `PwAnomalyDetail`; `property_watch_id` list param.
- `frontend/src/lib/nav.ts` (+ `nav.test.ts`): "Property watches" in Behavior.
- `frontend/src/lib/anomalyQuery.ts`, `app/anomalies/page.tsx`, `app/anomalies/[id]/page.tsx`, `components/behavior/AnomalyFilters.tsx`, `components/behavior/AnomalyRows.tsx`: the `PW_VALUE` kind, its detector group, a `property_watch_id` filter and chip, the row, the investigation view.
- Existing tests extended, not weakened: `app/anomalies/waf.page.test.tsx` and `ips.page.test.tsx` (each pinned the exact subject list; the new kind is appended), `lib/nav.test.ts` (list gains the entry; title "four" became "five").
- Not done: the `[BUILT — PW-7]` markers in `docs/PHASE-G-PROPERTY-WATCH.md` (open point 3).

## 2. What was done

- Navigation; the list; the watch page (header, series chart with the parsed-share panel on the same UTC axis and the floor line, values table, honesty line, Values / Incidents / Definition tabs, windows 1 h, 6 h, 24 h, 7 d); PW rows, filter and investigation view in `/anomalies`. No Threshold rules tab, no Edit, no New watch link. PW-8 adds "New watch" in the `PageHeader` of `app/behavior/watches/page.tsx` (comment there) and its Edit disclosure in `WatchWorkspace.tsx`.
- Shared chart `PropertyWatchSeriesChart`: props `series`, `bucketSeconds`, `windowStart/End`, `collected`, `coverage {buckets, floor}`, `incidents`, `compact` (widget form), `emptyText`. It reads nothing itself, so PW-8's preview and PW-9's widget feed it the same props.
- Honesty rules: uncollected buckets, UNKNOWN and not-tracked readings are gaps (`connectNulls: false`); a measured zero is 0; a lower bound is "at least n" (tooltip, table, hollow marker, key); the band only for one value (one line says so otherwise); at most five series in `--chart-cat-1..5`; incident and uncollected spans shaded; y-axis "events per 5 min"; every tooltip string holding a value goes through `format.encodeHTML` (the T-117 test feeds `<img src=x onerror=…>`).
- Values table: pinned first, right-aligned tabular figures, unit in caption, "Learning (n of 168 cells)", share of parsed events only for an exact value in an exact bucket with a whole total. Sorting by links, paging by `Pagination`, selection by checkbox (max five, the sixth disabled), one bulk series read per new selection.
- States designed and tested: loading, empty, error, forbidden (names `read:property_watches`), collection off (names `PW_COLLECTION_ENABLED`), shadow mode (`PW_ALERTING_ENABLED`), every `PwWatchStatus` and "never run", PARTIAL / TRUNCATED / NOT COLLECTED (counted in intervals), admission, membership change, NOT_APPLICABLE, SCOPE_STALE, SCOPE_TOO_LARGE, stale.

Decisions taken without the orchestrator (the more honest side each time):
- The API serves no per-value verdict, so the state column reads "Baseline reliable / Learning (n of 168 cells) / No baseline yet" plus any active incident of that value. It never says "Normal".
- `GET /anomalies/{id}` answers 404 for a `pw_anomaly` row in this branch. The PW investigation view is built on the list-item shape plus `transitions` / `policy_version` (the usual detail additions) and was drawn from a fixture of that assumed shape. Until a backend task serves it, the page shows "does not exist".
- The anomaly row carries the value text but not its id, so the investigation page finds the id among the watch's first 200 tracked values and links `?value=<id>`. If it is not found it links to the watch without a selection and says the chart is absent. A value's text is never in a URL.
- The new-value detectors' figure has no known unit: the list shows an em dash with a screen-reader reason; the investigation view shows the count with "the unit is the detector's own".
- The generated AQL is not served by any read route (only the writer-only preview carries it), so Definition has no `CodePanel`. No placeholder.
- Scope membership in Definition was built against PW-5b's `WatchScopeOut` in `/home/efe/Documents/QRadar-Dash-wt/pw5b/backend/app/schemas/property_watch.py` (the model appeared while I worked; the route exists there). `WatchScope` in `lib/api.ts` is typed from it; if PW-5b renames a field it must follow.
- A bug the screenshots found: the server page imported a constant from a `"use client"` module and failed at render ("Attempted to call VALUES_PAGE_SIZE() from the server"), invisible to component tests that mock the module. Fixed (constant moved to `pwRange.ts`) and pinned by a source-reading test.

## 3. Gates (from `frontend/`, `~/.local/node/bin` on PATH)

- `npm ci`: completed (own `node_modules`).
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, exit 0, no output.
- `npx vitest run --no-cache`: `Test Files  1 failed | 301 passed (302)` and `Tests  1 failed | 3354 passed (3355)`. The one failure is `src/widgets/registry.test.ts > requires only permissions the backend defines, and the list cannot drift from rbac.py`: `rbac.py` in this branch defines `read:property_watches` and `property_watch:write`, which `frontend/src/widgets/registry.ts` does not list. It is not in my allowed files and belongs to PW-9; I did not touch it. `colourLiterals.test.ts`, the contrast tests and `TableNames.test.ts` pass.
- `npm run build`: `✓ Compiled successfully`; `/behavior/watches` and `/behavior/watches/[id]` are in the route table.

## 4. Break-it results (break made, named tests run, restored)

| Break | Result |
|---|---|
| `encodeHTML` removed from the tooltip | 2 tests failed |
| `connectNulls: true` | 2 failed |
| UNKNOWN reading drawn as 0 | 2 failed |
| NO_BUCKET reading drawn as 0 | survived; test added; now 1 fails |
| band drawn for several values | 1 failed |
| series cap 6 | 2 failed |
| "at least" removed from `formatEvents` | 3 failed |
| never-run status toned `ok` | 1 failed |
| missing count formatted as `0` | 1 failed |
| server page imports a value from a client module | 1 failed |

## 5. Screenshots

In `/home/efe/Documents/QRadar-Dash-work/pw7/`, named `NAME-1440x900.png`, `NAME-1280x800.png`, `NAME-1024x768.png`, and `NAME-WxH-full.png` for the whole page. Page overflow was measured in headless Chrome for every one: `scrollWidth === clientWidth` at all three viewports (no overflow). Fixture API `fixture_api.py`, script `capture.mjs`, ports 3117 / 8117 / 9117, all outside the repository.

- `list-statuses`: OPEN, STALE, NOT_APPLICABLE, SCOPE_STALE, SCOPE_TOO_LARGE first, then DISABLED, RECOVERING, OK, never run; shadow-mode banner; latency sentence once; "at least 93 %" on lower-bound coverage; last bucket in UTC with its age; "no bucket yet".
- `list-empty`, `list-collection-off` (banner naming `PW_COLLECTION_ENABLED`), `list-forbidden` (names `read:property_watches`).
- `detail-exact`: five values (two pinned), uncollected spans, incident spans, parsed-share panel with floor line, table, honesty notes. A hostile `<img…>` value and a very long value render as wrapped text.
- `detail-lower-bound`: hollow markers, "At least this many" key, "at least n" in the table, no share for lower-bound values, TRUNCATED and ADMISSION notes.
- `detail-learning`: "Learning (n of 168 cells)", "learning" in the range column, no band.
- `detail-not-applicable`: no values; the parsed share drawn at 0 % under the 90 % floor; the NOT APPLICABLE line.
- `detail-scope-stale`: SCOPE STALE in header and honesty line; the series ends where collection stopped.
- `detail-one-value-band`: one value with its band. `detail-five-values`: five lines and the "select a single value" line.
- `detail-incidents`, `detail-definition` (with scope membership), `anomalies-pw` (four PW rows incl. the hostile value, unit under each figure), `anomaly-pw-detail` (identity, measured figures with unit, the value's series with incident span, lifecycle).

I opened and read: list-statuses 1440, detail-exact 1440 and 1024 (full), detail-lower-bound 1440 (full), detail-one-value-band 1440, detail-not-applicable 1280, anomaly-pw-detail 1280 (full), anomalies-pw 1024. At 1024 the values table scrolls inside its own region (allowed by guidelines §9); the page does not. The Definition and scope-stale captures were re-taken last with the membership part (read: definition 1024 full).

## 6. Open points

1. `GET /anomalies/{id}` for a PW row is 404 on the backend. Until it serves the extension the investigation view is only reachable in the fixture. A backend task is needed; a `value_id` in `PwValueRefOut` would remove the value lookup.
2. `registry.test.ts` fails on the missing PW permissions (PW-9's file). Left as it is it keeps the vitest gate red.
3. The `[BUILT — PW-7]` markers in `docs/PHASE-G-PROPERTY-WATCH.md` §G.13.1–§G.13.3 are not written. Left out, the design doc does not say the UI exists.
4. The honesty line is last in the Values tab, as §G.13.3's order says; at 1440×900 it is below the first screen. Moving it above the chart is a one-block move in `WatchWorkspace.tsx`.
5. Definition has no generated AQL (no read route) and there is no Threshold rules tab (PW-6). The new-value detectors' figures have no unit until PW-3b fixes one.
6. Other `/anomalies` consumers that call the unified list without a `subject_kind` would now receive PW rows whose `log_source_id` is null while the type still says `string`. The callers I found filter by kind.
7. The fixture is a fixture: the PW anomaly detail shape and the series were assumed, not read from a running backend. `WatchScope` follows pw5b's uncommitted schema.
