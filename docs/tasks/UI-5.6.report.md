# UI-5.6 report: log sources, searches and alerts reviewed and fixed

## 1. Files changed
- `frontend/src/app/log-sources/page.tsx`, `log-sources.css` (new): `PageHeader`, table in `TableScroll`, unknown status neutral, no count on a failed read, last-event time with zone, `num` columns, maintenance marker spacing, name column floor.
- `frontend/src/app/log-sources/[id]/page.tsx`, `log-source-detail.css` (new): inline styles moved to the stylesheet, last-event time with zone, designed "no health score" state, unit on the overall score, error notice with a recovery line.
- `frontend/src/app/searches/page.tsx`: `PageHeader`, dense table, designed error notice.
- `frontend/src/app/searches/[id]/page.tsx`, `search-detail.css` (new): stored AQL in `CodePanel`; the trend caption's inline style became a class.
- `frontend/src/app/alerts/page.tsx`, `alerts.css` (new): `PageHeader`, table in `TableScroll`, times with zone, `num` column, designed error notice.
- `frontend/src/app/alerts/[id]/page.tsx`, `alert-detail.css` (new): times with zone, delivery table in `TableScroll`, inline styles moved to the stylesheet, error notice with a recovery line.
- `frontend/src/components/AlertActions.tsx`, `ResultTrend.tsx`: inline styles replaced by classes (defined in the two route stylesheets above; no new markup).
- Six `page.test.tsx` files: the six T-096 `it.todo` lines became real tests; new tests added (below).
- `docs/tasks/UI-5.6.report.md`: this file.
- No new token was added. `tokens.css`, `globals.css`, `components/ui/*` and `lib/*` are untouched.

## 2. Defect list
Captured 2026-10-15 10:30 to 10:31 (+03) before any edit (`png/before/`, 60 files); the after set was captured 10:38 to 10:43.

| # | Route / viewport | What is wrong | Rule | Fix |
|---|---|---|---|---|
| 1 | /log-sources, all | Subtitle read "0 sources" after a failed read | §3a no zero for a missing measurement (T-096 #3) | Count only on a successful read |
| 2 | /log-sources, all | Unknown QRadar status drawn as `crit` | rule 8 (T-096 #4) | Null status is a neutral pill |
| 3 | /log-sources, all | Last-event time: viewer's locale, no zone (shown as `8:59:40 PM`) | §3a times show a timezone (T-096 #5) | `formatDateTimeCompact` ("10-14 17:59 UTC"), same formatter as the anomaly tables |
| 4 | /log-sources/[id], all | Last-event time, no zone (T-096 #6) | same | `formatDateTime` ("2026-10-14 17:59:40 UTC") |
| 5 | /log-sources/[id], all | 3 inline styles | §3a no inline styles | Classes in `log-source-detail.css` |
| 6 | /alerts, all | First/last seen, no zone (T-096 #7) | same | `formatDateTimeCompact`, nowrap |
| 7 | /alerts/[id], all | Opened and delivery times, no zone (T-096 #8) | same | `formatDateTime` for the lifecycle, compact in the dense delivery table |
| 8 | all six, all | Page titles were bare `h2` + `.subtitle`: title sat at y=132 against 103 on pages built on `PageHeader`, and the page had no `h1` | UR.3.3; consistency | `PageHeader` on the four routes whose tests allow it. The two detail pages are open point 1 |
| 9 | /log-sources, /alerts, 1024 | Tables not in `TableScroll`. /alerts/[id] 1024: delivery table 797 px in a 765 px container, so the document scrolled sideways (scrollWidth 1017 over 1009) | §9 | `TableScroll` on every table. No page overflow after, on any capture |
| 10 | /log-sources, /alerts, 1024 | Source name wrapped to 7 lines, alert title to 9 | UI-5.4 reviewer's hostname finding | Compact times, a 22ch / 18ch floor on the name and title columns. Now 2 to 3 lines |
| 11 | /alerts/[id], 1024 | The delivery table had to scroll sideways and hid the Error column, where a dead-letter reason lives | §9, honesty | Compact times, wrapping at the space, Error floor 12ch. Table fits (765 of 765) |
| 12 | /log-sources, /alerts | Health, Anomalies, Occurrences, Attempts not right-aligned | §3a numbers right-aligned | `num` on header and cell |
| 13 | /log-sources | `maint` pill touched the source name | spacing | `.log-source-maint` margin |
| 14 | /log-sources/[id] | `health_breakdown` null: the cards simply vanished | UR.1.2 honesty state | A `role="status"` notice: "No health score yet. ..." |
| 15 | /log-sources/[id] | Overall score had no scale | numbers carry units | "/ 100" after the score |
| 16 | /searches/[id] | Stored AQL in a bare `<pre>` in a `.card` | UR.1.2: AQL only in `CodePanel` | `CodePanel` (collapsed, copy button, scrolls inside itself) |
| 17 | all error states | Terse ("Backend unreachable.", "Could not load alert X.") | §3a error says what failed and what to do | Same pinned first sentence, plus a recovery line; `role="alert"` |
| 18 | /alerts/[id] | AlertActions: 4 inline styles | §3a | Classes |
| 19 | /searches/[id] | ResultTrend caption: inline style | §3a | Class `trend-note` |

Measured after (document scrollWidth equals clientWidth on every one of the 60 captures; tables inside their scroll container): /log-sources 1024 780 of 780; /alerts 1024 780 of 780; /alerts/[id] delivery 765 of 765; /searches/[id] 765 of 765.

## 3. What changed per route (before and after PNGs looked at)
- /log-sources: `h1` header at the shared position, a count only when read, times in UTC, unknown status grey, right-aligned figures. 1024: names fit in 2 to 3 lines (before 7).
- /log-sources/[id]: same header scale; the five health cards unchanged apart from the unit; no-breakdown notice (sparse capture); times in UTC.
- /searches: header and copy unchanged, the error notice says what to do. Layout was already sound.
- /searches/[id]: the AQL is a collapsed read-only panel under its section heading. The truncated runs ("≥ 500", "no verdict", "truncated, count not recorded") and the trend gap caption were already honest and are unchanged.
- /alerts: `h1` header, "Times are UTC.", compact UTC times, titles readable at 1024, table fits.
- /alerts/[id]: UTC times, delivery table (including the dead-lettered row with its full error) fits at 1024; open, acknowledged, resolved, no-notification and error states captured.

PNGs (60 each, `{route}-{1440x900,1280x800,1024x768}.png`, tall pages captured at full height):
- Before: `/home/efe/Documents/QRadar-Dash-work/ui56/png/before/`
- After: `/home/efe/Documents/QRadar-Dash-work/ui56/png/after/`
- Routes: `log-sources`, `log-sources-empty`, `log-sources-error`, `log-source-detail`, `log-source-detail-sparse`, `log-source-detail-error`, `searches`, `searches-empty`, `searches-error`, `search-detail`, `search-detail-noexec`, `search-detail-error`, `alerts`, `alerts-empty`, `alerts-error`, `alert-detail-open`, `alert-detail-ack`, `alert-detail-resolved`, `alert-detail-nonotif`, `alert-detail-error`.
- Measurements: `.../ui56/logs/{before,after}-measurements.json`. Fixture (backend payload shapes): `.../ui56/fixture/`.
- I opened about 20 of the 60 after PNGs and 8 of the before PNGs: every route at least once at 1024 or 1440 (empty and error states, sparse and no-execution states, open, resolved and no-notification alerts, search detail). The others were checked by their overflow and table-width measurements only, not by eye.

## 4. Tests
- Six `it.todo` converted to real tests (log-sources 3, log-sources/[id] 1, alerts 1, alerts/[id] 1). Todo count 8 to 2; the two left are T-096's offenses/[id] defects, outside this task.
- New tests (about 20): h1 on each list page, scroll regions, `num` alignment (cell and header), error `role="alert"` with the recovery line, known non-SUCCESS status still `crit`, no health breakdown notice, no inline style on the detail pages and in AlertActions, delivery table region, stored AQL only in `details.code-panel` with no textarea.
- Break-it: 20 single-line mutations (revert a fix, drop a class, swap a label, re-add an inline style) each run against the matching test file. 19 failed at once. One (`th.num` on Health) survived; I added the header assertion, re-broke it (failed), restored it (passes). All sources were restored after each run (script `.../ui56/breakit.py`).

## 5. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output, exit 0
- `npx vitest run --no-cache`: `Test Files  188 passed (188)` / `Tests  2519 passed | 2 todo (2521)`
- `npm run build`: `✓ Compiled successfully in 5.9s`
Processes started (Next, Chrome, fixture) were stopped by PID.

## 6. Open points
1. **No `h1` on `/log-sources/[id]` and `/alerts/[id]`.** Their tests pin the title as `level: 2`, so I kept an `h2` and gave it the `PageHeader` look locally (`.main .detail-header .page-title`). Fix: change those two assertions to level 1 and use `PageHeader` (a two-line test edit, not mine to make).
2. **`Evidence.tsx` (shared) uses inline styles and no units** ("Observed 182.4", "Confidence 0.94"). Not in the allowed files. The alert-detail "no inline style" test excludes `.alert-evidence` for this reason.
3. **No `loading.tsx` or `error.tsx`** under these six routes (Anomalies and Fleet have one). New files are outside the allowed list. Pages catch their own read errors, so the gap is the loading state only.
4. **No confirmation on Resolve** (terminal state) or on "Run now" (costs an Ariel query). Adding one changes the pinned click-through behaviour of `AlertActions` and `RunSearchButton`. A decision for the orchestrator.
5. `/searches/[id]` Execution history at 1024: `Started (UTC)` wraps to two lines ("10-14 17:55 / UTC") on every row. `ExecutionsTable` is allowed, but the table has no spare width. Left.
6. "1 sources" (no singular) is pinned by an existing test; not changed.
7. QRadar statuses other than SUCCESS (for example WARNING, DISABLED) are all toned `crit`, as before; only null became neutral. Whether WARNING should be `warn` is a product call.
8. The Phase-2 placeholder sentence on the log-source detail ("EPS trend ... arrive in Phase 2") is pinned by a test and still shown.
9. Empty notices "No alerts." / "No scheduled searches defined yet." are pinned exact strings and do not say why; `/alerts`' description does explain the lifecycle.
