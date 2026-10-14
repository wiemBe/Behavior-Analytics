# UI-2.5 report

## Files changed

- `frontend/src/app/page.tsx` — replaced the old server-rendered SOC overview with the Dashboard shell and metadata.
- `frontend/src/app/loading.tsx` — added the route's polite plain-text loading state.
- `frontend/src/app/error.tsx` — added the route's actionable, non-leaking error state.
- `frontend/src/app/page.test.tsx` — added hierarchy, composition, data-age, empty, permission, backend-down, insufficient-data and route-state coverage.
- `frontend/src/app/tokens.css` — added the two scroll-edge width tokens.
- `frontend/src/components/home/HomeDashboard.tsx` — composed the KPI row and seven widgets and reported fleet data age to the top bar.
- `frontend/src/components/home/dashboard.css` — added the fixed 12-column Home layout and its 1024-width reflow.
- `frontend/src/components/home/WidgetFrame.tsx` — added the one shared KPI/panel frame, all resource states, qualifier presentations and header controls slot.
- `frontend/src/components/home/WidgetFrame.test.tsx` — covered idle/loading, forbidden, error, ok, refresh-failed and all four qualifier kinds.
- `frontend/src/components/home/KpiParts.tsx` — retained the KPI-only figure, line and count primitives after frame consolidation.
- `frontend/src/components/home/KpiFrame.tsx` — removed the duplicated KPI frame after all KPI cards moved to `WidgetFrame`.
- `frontend/src/components/home/WidgetParts.tsx` — reduced this file to table-body primitives; frame, gate and qualifier duplication moved to `WidgetFrame`.
- `frontend/src/components/home/KpiTotalEps.tsx` — moved the card to the shared frame.
- `frontend/src/components/home/KpiLogSources.tsx` — moved the card and its qualifiers to the shared frame.
- `frontend/src/components/home/KpiOffenses.tsx` — moved the card and its qualifiers to the shared frame.
- `frontend/src/components/home/KpiAnomalies.tsx` — moved the card to the shared frame and made a fresh install explicitly insufficient rather than a measured zero.
- `frontend/src/components/home/KpiAnomalies.test.tsx` — pinned the fresh-install em dash and insufficient-data explanation.
- `frontend/src/components/home/EpsChart.tsx` — moved the chart to the shared frame and put window presets in its header slot.
- `frontend/src/components/home/EpsByClass.tsx` — moved the chart to the shared frame and standardized list, total and donut EPS formatting.
- `frontend/src/components/home/EpsByClass.test.tsx` — updated only the two EPS strings required by the standard formatter.
- `frontend/src/components/home/SeverityChart.tsx` — moved the chart and its state gate to the shared frame.
- `frontend/src/components/home/TopSources.tsx` — moved the table to the shared frame, standardized EPS, and kept the middle dot with the folded state word.
- `frontend/src/components/home/TopSources.test.tsx` — updated only the one EPS string required by the standard formatter.
- `frontend/src/components/home/LogSourceHealth.tsx` — moved the widget and its qualifiers to the shared frame.
- `frontend/src/components/home/RecentAnomalies.tsx` — moved the widget to the shared frame.
- `frontend/src/components/home/RecentOffenses.tsx` — moved the widget to the shared frame and used the abortable `homeReads` request.
- `frontend/src/components/home/RecentOffenses.test.tsx` — changed the mocked key source from the removed helper to `homeReads`; assertions were not weakened.
- `frontend/src/components/home/chartOptions.ts` — standardized Fleet EPS, class donut and tooltip values on `formatEpsValue`.
- `frontend/src/components/home/charts.css` — removed the obsolete under-chart controls row and sized the header presets.
- `frontend/src/components/home/tables.css` — replaced the duplicated scroll-edge pixel widths with tokens.
- `frontend/src/lib/api.ts` — added only `homeReads.recentOffenses`, including `AbortSignal` forwarding through `homeRead`.
- `frontend/src/lib/home/recentOffenses.ts` — removed the non-abortable request helper while retaining the pure view derivation.
- `frontend/src/lib/home/recentOffensesRead.test.ts` — pinned the request identity, order, limit and exact signal forwarding.
- `frontend/src/lib/home/format.ts` — removed the superseded Home-only EPS formatter.
- `frontend/src/lib/home/format.test.ts` — removed only that deleted formatter's tests; the remaining formatter tests are unchanged.
- `docs/tasks/UI-2.5.report.md` — recorded implementation, inherited-work review, gates and screenshot evidence.

## What I did

### Composition and fix list

- `/` is now the dark Dashboard: one factual header line, four compact KPIs, Fleet EPS 8/12 plus EPS by class 4/12, three 4/12 investigation widgets, and two 6/12 recent-item tables. Geography is absent and there is no placeholder.
- The page remains a server shell around the client composition. Every widget reads through `useApiResource`; identical request keys share the hook cache and the shell refresh tick revalidates them. `HomeDashboard` reports the 24 h fleet series `window_end` through `useReportDataAge`.
- `WidgetFrame` is the sole Home frame and sole primary-resource state gate. It supports KPI and panel density, loading, forbidden, error, stale-after-refresh, all honesty qualifiers, description, badge, drill-down and header controls.
- `homeReads.recentOffenses` requests `status=OPEN&sort=last_updated_time&limit=5` and forwards the hook's `AbortSignal`.
- The Top sources folded state is one non-wrapping span containing both `· ` and the following state, so the separator cannot begin a line.
- The table cover/shadow widths are `--scroll-cover-w` and `--scroll-shadow-w` tokens.
- Silent and Exceeding expected volume still link to `/anomalies?anomaly_type=NO_EVENTS` and `/anomalies?anomaly_type=VOLUME_SPIKE`. The anomalies route does **not** parse, preserve or forward an `active_only` search parameter, so per the task I did not add it to these links.
- Every Home EPS presentation now calls `formatEpsValue`: KPI, Top sources, EPS-by-class list and donut centre, Fleet EPS tooltip and class tooltip. The obsolete `formatEps` implementation and its two tests were deleted as explicitly required.
- Existing EPS expectation edits, and no others: `EpsByClass.test.tsx` changed `750` to `750.00` and accessible total `1,000 EPS` to `1,000.00 EPS`; `TopSources.test.tsx` changed `1,000` to `1,000.00`.
- Fleet EPS presets now sit in the shared title row beside the window/qualifier and before the Fleet drill-down.
- KPI tops align. Grid tracks take the tallest card's height while `align-items: start` keeps shorter card content compact instead of stretching it or adding filler.
- At 1280 the content is still 12 columns (Fleet EPS 652 px, class chart 320 px, recent tables 486 px). At 1024 it reflows (Fleet EPS and each recent table 780 px; paired widgets 384 px). All 15 captures reported document width equal to client width and no widget overflow.

### Page states and tests

- Fresh install: absent EPS/anomaly measurements are em dashes with explicit `INSUFFICIENT_DATA`/`NOT_COLLECTED` reasons; lists and charts explain why they are empty.
- Partial permission: only the offense KPI and Recent offenses state the missing `read:offenses` permission; all other widgets render normally.
- Backend down: every widget contains its own named failure and tells the analyst to refresh/check reachability.
- Insufficient data: gaps stay neutral and undrawn rather than becoming zero or healthy.
- Route loading is a polite live region; the error fallback names the failed route and the recovery action without exposing an exception.

### Break-it checks

Each mutation was made locally, its focused test was observed failing, and the implementation was restored before the final suite:

1. Changed the page title from `Dashboard` to `Overview`: two page tests failed (hierarchy and backend-down shell guard).
2. Removed `RecentOffenses` from the composition: four page tests failed, including the expected 11-to-10 error-state count.
3. Made the shared forbidden branch render loading: the WidgetFrame permission-copy test failed.
4. Suppressed the refresh-failed note: the WidgetFrame stale-data test failed.
5. Disabled the fresh-install anomaly guard: two tests failed, including the em-dash/no-zero guard.
6. Replaced EPS-by-class formatting with a plain locale string: the expected `750.00` assertion failed.

### Inherited work review

- **Kept:** the server/client split, the initial `HomeDashboard` composition, shared-frame direction and `KpiFrame` deletion, `KpiParts`, abortable recent-offense read/test, data-age read, header presets, scroll tokens, separator grouping, EPS migration, compact KPI styling and the chart/table visual design.
- **Changed/completed:** corrected the Home container breakpoint from 1100 px to 900 px so 1280 stays 12-column while 1024 reflows; completed shared qualifier ownership and every widget migration; completed direct `formatEpsValue` use; added the fresh-install anomaly guard; tightened the header copy; added route fallbacks, the page test and WidgetFrame test; completed break-it verification and the five-scenario screenshot matrix.
- **Reverted:** no inherited change was reverted wholesale. Temporary break-it mutations were all restored. No existing assertion was weakened; apart from the three allowed EPS strings above, existing assertions were retained.

### Screenshot review

Every PNG below was opened and visually inspected. Viewport and full-page images show no clipping, page overflow, nested cards, placeholder map, default chart styling or unlabelled error/permission state.

- **1440×900 first screen:** page header, all four KPI cards, both complete primary charts, and the top of the Top sources / severity / log-source-health row. The KPI row remains compact and the chart controls are in the Fleet EPS header.
- **1280×800 first screen:** page header, all four KPI cards and both primary charts (their lower edge reaches the viewport); the layout remains 8+4 rather than prematurely reflowing.
- **1024×768 first screen:** page header, the KPI cards in a two-by-two layout and the Fleet EPS chart through its plotted area; following widgets reflow to two-up, with both recent tables full width farther down.

Screenshot root: `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/`

- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/ok-1440x900-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/ok-1440x900-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/ok-1280x800-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/ok-1280x800-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/ok-1024x768-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/ok-1024x768-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/empty-1440x900-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/empty-1440x900-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/empty-1280x800-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/empty-1280x800-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/empty-1024x768-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/empty-1024x768-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/partial-permission-1440x900-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/partial-permission-1440x900-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/partial-permission-1280x800-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/partial-permission-1280x800-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/partial-permission-1024x768-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/partial-permission-1024x768-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/down-1440x900-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/down-1440x900-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/down-1280x800-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/down-1280x800-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/down-1024x768-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/down-1024x768-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/insufficient-1440x900-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/insufficient-1440x900-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/insufficient-1280x800-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/insufficient-1280x800-full.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/insufficient-1024x768-first.png`
- `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/insufficient-1024x768-full.png`

## Gates

Run from `frontend/` with `~/.local/node/bin` on `PATH`:

- `npm run lint` — exit 0. Summary: `✔ No ESLint warnings or errors`
- `npm run typecheck` — exit 0; `tsc --noEmit` produced no diagnostics.
- `npm test` — all tests passed, but the command exited 1 after the test summary because the required shared `node_modules` symlink is read-only and Vitest tried to save `node_modules/.vite/vitest/results.json`. Verbatim summary:
  - `Test Files  137 passed (137)`
  - `Tests  1973 passed (1973)`
  - `Duration  29.89s (transform 20.46s, setup 77.34s, collect 57.46s, tests 208.32s, environment 201.87s, prepare 37.20s)`
  - Post-summary error: `Error: EROFS: read-only file system, open '/home/efe/Documents/QRadar-Dash-wt/ui25/frontend/node_modules/.vite/vitest/results.json'`
- `npx vitest run --no-cache` — exit 0, confirming the full suite without the prohibited cache write. Verbatim summary:
  - `Test Files  137 passed (137)`
  - `Tests  1973 passed (1973)`
  - `Duration  29.45s (transform 22.08s, setup 79.54s, collect 60.67s, tests 198.15s, environment 201.75s, prepare 36.86s)`
- `npm run build` with the fixture API base URL in the environment — exit 0. Verbatim summary lines: `✓ Compiled successfully in 5.1s`, `✓ Generating static pages (25/25)`, `○  (Static)   prerendered as static content`, `ƒ  (Dynamic)  server-rendered on demand`.
- Final focused page/frame run: `npx vitest run --no-cache src/components/home/WidgetFrame.test.tsx src/app/page.test.tsx` — 2 files passed, 13 tests passed.
- `git diff --check` — exit 0, no output.
- Screenshot capture script — exit 0; 30 PNGs written. Its geometry audit reported `page_ok: true`, no overflowing widget, and no local table scroll at all 15 scenario/viewport combinations.

Other test attempts, reported for completeness:

- An early focused touched-widget run exposed four incorrect new test queries: 117 passed and 4 failed. The tests were corrected without changing existing assertions; the final focused/full results above are green.
- `npm test -- --runInBand` was an invalid Vitest invocation and failed before collection with unknown option `--runInBand`; it was rerun correctly.
- One initial `npm test` execution was terminated by the command wrapper at 30 seconds before its summary; the completed required invocation and no-cache verification are reported above.

The full suite emits pre-existing React `act(...)` warnings in AutoRefresh and AddIpsSourcePicker tests; these are warnings, not failures, and no related file was in scope.

## Open points

- The required literal `npm test` cannot exit 0 in this worktree because `frontend/node_modules` is the user-mandated symlink to the main checkout and is read-only. No install, permission change or write to the main checkout was attempted; the identical no-cache suite passes 1,973/1,973.
- The anomalies page has no `active_only` URL-query support, so the two health-category links retain only `anomaly_type`, as directed by fix (e).
- An inherited IPv4 Next listener from the interrupted agent already occupied `127.0.0.1:3125` with a stale PID that is not visible in this process namespace. I did not kill an untracked process. The current production build was served for captures on IPv6 loopback `[::1]:3125`, still port 3125; only the exact Next and fixture processes started in this session were stopped.
- Backend lint, pytest, mypy and integration tests were not run; this was a frontend-only task and no backend file changed.

## Round 2

Inherited state: Codex had implemented all 10 items; no break-it mutation was left in the code. One suite failure on entry (`src/app/page.test.tsx`, round-1 file, expected "No monitored log sources. Enable monitoring" twice; item 6 made the KPI copy singular) was fixed by widening the regex to `sources?`. Kept: item 1-8 and 10 implementation as found (fill-body charts, donut hole token, severity `interval: 5`, KPI stretch grid, nowrap parts, single empty statement, no sparkline when nothing measured, one `widget-error` structure, `active_only` page/filter/query).
Changed by me: item 9 and the separator placement (item 5a/5b).

### Per item
1. Charts: `.tw-body-fill` + `.hc-chart` flex, min height `--home-chart-h`; legend gap to card bottom 17 px for Fleet EPS and severity at all viewports.
2. Donut hole/label sized from `--home-donut-hole-radius` / `--home-donut-label-size`; label inside the hole with >= 15 px side margins (21 px for 842.31).
3. Severity x-axis: `interval: 5, hideOverlap`: four labels (14:00 20:00 02:00 08:00) + UTC.
4. KPI row grid `align-items: stretch`, body content stays at top; all heights equal per row.
5. (a) The "·" now trails the preceding part (`1 spikes ·`), so no line starts with it (Codex's leading-separator version still produced lines starting with "·" in the 1440 capture; I changed it). (b) counts joined to their noun with a non-breaking space, parts nowrap.
6. Log sources empty: one statement ("No monitored log source. Enable monitoring ..."), no inner box.
7. Sparkline: nothing drawn when no step is measured; the baseline rule was removed in the ok state too.
8. One `WidgetFrame` error: bold "what failed" line plus action paragraph, in kpi and panel; red only on the title.
9. Backend check (`api/routes/anomalies.py` summary): `open_anomalies` = all OPEN+CANDIDATE+RECOVERING; spikes/drops/silent only count three of five detector types, so they do NOT sum to the total (PARSING_DEGRADATION, UNKNOWN_EVENT_SPIKE exist); candidates/recovering are lifecycle states. Codex's "exact partition by detector" was wrong. Now two labelled lines: "By detector:" spikes, drops, silent plus an "other" remainder when > 0 (sums to total), and "Lifecycle:" open (= total - candidates - recovering), candidates, recovering (sums to total). "silent" counts NO_EVENTS anomalies. Fixture was already consistent (345 = 173+120+52; 18 = 9+6+3), no fixture change.
10. `active_only` parsed (only "true"), passed to `api.anomalies`, kept in paging links, and shown as an "Active only" checkbox in the filter bar plus a clearable chip; Silent and Exceeding links carry `&active_only=true`.

### Existing-test edits (all legitimate)
- `anomalies.test.ts`: renamed `byType` to `byDetector`/`byLifecycle`; original inputs restored (4/2/1), expects the new "other" 2 and lifecycle open 5.
- `KpiAnomalies.test.tsx`: candidates/recovering 2/3 -> 1/1 (3 total; 2+3 exceeded the open total, now an invalid summary); line assertions check parts and nbsp; error assertions check the new title/action structure.
- `KpiOffenses.test.tsx`: line text assertion kept (nbsp normalised) plus part assertions; error assertion adapted to the new structure.
- `KpiLogSources.test.tsx`, other Kpi*/Home widget tests: error text adapted to the single error structure, renamed classes.
- `Sparkline.test.tsx`: "no step measured" now asserts no svg and no caption (brief item 7).
- Round-1 EPS string edits (`750.00`, `1,000.00`) unchanged.

### Geometry audit (capture.py, 15 scenario x viewport runs; page_ok true everywhere)
- Donut label in hole (ok/partial 1440,1280,1024): inside=true, margins L/R 15.5, top 27, bottom 42 px; insufficient L/R 21 px.
- Severity ticks: no overlap; min gap between labels 17.9 px (1440), 3.9 px (1280), 20.6 px (1024). The audit estimates this from the chart width and the measured label width; it does not read ECharts' own layout. 1280 is the tightest.
- Legend-to-card-bottom gap: Fleet EPS 17 px, severity 17 px at all viewports.
- KPI heights per row (delta 0 everywhere): ok 1440 209.5, 1280 231.5, 1024 190 / 160; insufficient 217 / 231.5 / 190+160; empty 178 / 178 / 138+141; down 184.8 / 220.1 / 143.2.

### Break-it (each restored)
parse (3 fail), pass-through (1), paging (2), Silent/Exceeding links (1), sparkline rule (1), nbsp (2), trailing separator (1), "other" detector remainder (1), inner empty box (1), tick interval (1), error structure (2). Items 1, 2 and 4 are CSS and jsdom cannot see them; the geometry audit is their guard (not broken by hand).

### Gates (frontend/, node_modules is a read-only symlink, so `npx vitest run --no-cache` instead of `npm test`)
- `npm run lint`: ✔ No ESLint warnings or errors
- `npm run typecheck`: clean
- `npx vitest run --no-cache`: Test Files 137 passed (137); Tests 1981 passed (1981)
- `npm run build`: ✓ Compiled successfully; ✓ Generating static pages (25/25)
- `git diff --check`: clean

### PNGs (30 same names, overwritten, plus the new one), in /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/
First screens: 1440x900 shows header, four equal-height KPIs, Fleet EPS and class donut filled, top of the 3-up row; 1280x800 the same with KPIs taller; 1024x768 header, KPIs 2x2 and Fleet EPS. Scenarios: ok, empty, partial-permission, down, insufficient, each `<scenario>-<WxH>-first.png` and `-full.png`.
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/png/anomalies-active-only-1440x900-first.png (Active only checked, chip "Active only x", Clear all)

## Orchestrator review (2026-10-14)

Round 1 (Codex) and round 2 (Codex, cut off by its usage limit at 16:39 mid break-it pass, then
Sonnet with "review what you inherit") accepted. The orchestrator looked at the round-2 PNGs (ok
at the three viewports, empty at 1440, the `/anomalies` active-only capture), read every removed
assertion in an existing test file (each is replaced by an equivalent or stronger one; the changed
values follow from the item-9 partition fix), and re-ran the gates on the worktree: lint clean,
typecheck clean, `npx vitest run --no-cache` 137 files / 1,981 tests passed, build compiled.
Accepted leftovers, logged for later: at 1024 the EPS-by-class widget has ~100 px of spare card
(its row height comes from Top sources); in the empty scenario the health table lists true zeros
under the empty statement; the `/anomalies` Evidence cell truncates at 1440 (UI-5.2).
