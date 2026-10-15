# UI-5.1 report: behavior overview and source pages reviewed and fixed

## 1. Files changed
- `frontend/src/components/behavior/VolumeChart.tsx`: new exported `mergeIntervals`; anomaly bands are merged before drawing, so overlaps keep one intensity.
- `frontend/src/components/behavior/SourceQuality.tsx`: `collectionUnknown` state (failed metric read); the "of N" count never reads below its numerator.
- `frontend/src/components/behavior/SourceAdvanced.tsx`: `bucketsFailed`; "not loaded" notes instead of "0 cells" / "0 most recent".
- `frontend/src/components/behavior/SourceFilters.tsx`: `source-filters` class on the form (`filters` kept).
- `frontend/src/app/behavior/page.tsx`, `overview.css` (new): worklist table class, one dash for "neither measured", recovery line on the error, `Times are UTC.` kept together.
- `frontend/src/app/behavior/sources/page.tsx`, `sources/sources.css` (new): table and Detail-column hooks, recovery line on the error.
- `frontend/src/app/behavior/sources/[id]/page.tsx`: `PageHeader` plus back link on the error/404 state; failed metric read no longer yields collection claims.
- New tests: `VolumeChart.overlap.test.tsx`, `app/behavior/page.states.test.tsx`, `sources/page.states.test.tsx`, `sources/[id]/page.states.test.tsx`.
- No token added. No pinned test edited.

## 2. Defect list (captured 2026-10-15 12:22 to 12:25 before, 12:44 after)
| # | Route / viewport | What is wrong | Rule | Fix |
|---|---|---|---|---|
| 1 | /behavior/sources/[id], all | Overlapping incidents painted two translucent bands, so the overlap was darker (stronger signal) | task known defect | `mergeIntervals`; one band per merged span; 3 tests |
| 2 | /behavior, 1280 and 1024 | Worklist 1086 px in 969 and 973 in 765: Started and Investigate columns cut off | §9 | Name capped at 18ch and clamped to 2 lines (title keeps the full name), tighter cell padding under 1200 px; 969/969 and 765/765 |
| 3 | /behavior, 1280 | INSUFFICIENT_DATA pill lines overprinted each other | UX §4 | `pill-wrap` gets a readable line height at every width |
| 4 | /behavior, 1024 | Times in the worklist wrapped ("10-15 09:50 / UTC") | UI-5.6b: times never wrap | nowrap on `cell-when` |
| 5 | /behavior | "— / —" when neither value was measured | §3a missing is one dash | one "—" |
| 6 | /behavior | Header ended on an orphan "UTC." | UI-5.6b | "Times are UTC." kept whole |
| 7 | /behavior, 1024 | Seventh counter alone on a second line | spacing | tighter column gap |
| 8 | /behavior/sources, 1024 | Name column 1 word per line (long name: 11 lines); Filter button alone on a 2nd row | §9, UR.8.6 | name floor 16ch, Detail column (same target as the name link) hidden under 1200 px, search field yields width; 6 lines for the 92-char name |
| 9 | /behavior/sources/[id], failed metric read | "Collection health: Delayed", "0 of 360 fully observed", "Collection delayed" note, "0 cells", "0 most recent": zeros and verdicts for a read that never happened | rule 8, §3a | "Unknown" pill, dashes, "not loaded"; no collection note |
| 10 | /behavior/sources/[id] | "73 of 72 fully observed" (both window edges stored) | numbers | denominator never below the numerator |
| 11 | /behavior/sources/[id], all | Panel headings pushed down by the section margin (empty band above "Baseline quality") | spacing | `margin: 0` inside `.quality-grid` panels |
| 12 | /behavior/sources/[id], 1280 | Last-event time wrapped inside its card | times never wrap | nowrap, `fs-sm` |
| 13 | /behavior/sources/[id] 404 and failure | Bare `h1.page-title` with no header block, no way back, no next step | UR.3.3, §3a | `PageHeader`, "All sources" link, recovery sentence (pinned first sentences kept) |
| 14 | /behavior, /behavior/sources errors | "backend may be unreachable" with no action | §3a | "Reload the page to try again; if it persists, check the backend health." |
Checked and fine: expected range is one band plus a dashed median; no marker per point; axes "Time (UTC)" and "Events per second (EPS)"; "Still learning", measured zero "0.00" beside "—"; one vs several anomalies; sort headers keyboard-operable links.

## 3. What changed per route, and PNGs looked at
Fixture: `/home/efe/Documents/QRadar-Dash-work/ui51/fixture/server.py` (backend payload shapes), harness `capture.mjs`, `cycle.sh`. 48 PNGs each in `png/before/` and `png/after/` (16 scenarios x 3 viewports): overview, overview-clean, overview-empty, overview-error, sources, sources-filtered-none, sources-empty, sources-error, source-detail-{open,normal,longname,learning,silent,nodata,fails,404}. Every after capture has document scrollWidth equal to clientWidth.
Looked at (before): overview 1024 and 1280, overview-clean 1024, sources 1024 and 1280, sources-error 1024, source-detail-open 1024, source-detail-learning 1280, source-detail-fails 1024, source-detail-404 1024. After: overview 1024 and 1280, sources 1024 (twice, after the filter fix), source-detail-open 1024 and 1280, source-detail-fails 1024, source-detail-404 1024, source-detail-silent 1280. Not looked at by eye (measured only): the 1440 captures, *-empty, *-filtered-none, source-detail-normal, -longname, -nodata, and 1280 of sources-error.
- /behavior: worklist fits at 1280 and 1024 with Started and Investigate visible; one-line times; overlap-free pills.
- /behavior/sources: filter row on one line at 1024; names readable; table 765/765.
- /behavior/sources/[id]: overlapping bands uniform (open at 12 h: bands from the two incidents are one tint); failed-read page shows Unknown collection health and "not loaded"; 404 has header and back link.

## 4. Tests
New tests: 3 + 2 (overlap/merge), 5 (overview), 3 (sources), 7 (detail). Break-it: 10 single-line mutations (merge disabled; each prop, guard, class and sentence reverted); every one failed its test, then was restored. Pinned tests unedited and green.

## 5. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output
- `npx vitest run --no-cache`: `Test Files  200 passed (200)` / `Tests  2635 passed | 2 todo (2637)` (an earlier full run had one `HomeDashboard.test.tsx` failure on the loaded machine; that file passes alone and in the rerun)
- `npm run build`: `✓ Compiled successfully in 7.4s`
Processes (fixture, Next, Chrome) stopped by PID.

## 6. Open points
1. `stateMeaning` (lib/behavior.ts, not allowed) says "within the expected band" for a NORMAL source at 0.00 EPS against 31-48; only reachable with inconsistent data, but worth a look.
2. Shared `.sticky-actions` draws a vertical rule beside the Open/severity column on the anomaly tables (globals.css).
3. The four primary cards at 1024 (2x2) leave empty space under cards without a note (`StatCard`, `grid-auto-rows: 1fr`, shared).
4. The search placeholder is clipped at 1024 ("Search source name..").
5. 3 inline styles on the detail page are added at runtime by ECharts, not by our markup.
6. The long-name row on /behavior/sources still takes 6 lines at 1024.
