# UI-3.3b report

## 1. Files changed
- `frontend/src/lib/home/topSources.ts`: `topSources(..., limit = TOP_N)`.
- `frontend/src/components/catalog/derive.ts`: `newestSenders` and `classRows` take a `limit` (default = the card's).
- `frontend/src/widgets/runtime.tsx`: `useExpanded(prop?)`.
- `components/home/{TopSources,RecentAnomalies,RecentOffenses}.tsx`, `components/catalog/{FleetClasses,CatchallSenders,WafSources}.tsx`: expanded shows every row the read returned; "n of N" wording; request keys/limits untouched.
- `components/home/WidgetFrame.tsx`: only the status pill moves to the first body line in a panel narrower than 28 rem; range, Expand and link stay in the title row.
- `widgets/dashboard.css`, `app/tokens.css` (`--dash-select-w-tight`): narrow-panel title-row rules; the removed inherited `.tw-strip` rules.
- `components/home/charts.css`: removed `.hc-presets`/`.hc-preset` (grep over `src/` finds no other use).
- New tests: `components/home/expandedTables.test.tsx`, `components/catalog/expandedTables.test.tsx`; added two to `WidgetFrame.expand.test.tsx` (no existing test touched).

## 2. What I did
- The inherited design (pill + range + Expand on a strip in the body) was replaced: it left the controls on a second row.
- Title row (measured in Chrome, inner width of a 4-column panel = 334 px at 1440, 281 px at 1280, 343 at 1024):
  - Natural widths: title 152 / 177 / 168 px (eps_by_class / top_sources / anomaly_severity), link 30 / 47 / 64, Expand 48, select 48.
  - Chosen: pill to the body's first line (it is a 264 px sentence); the title's own "24 h" is hidden while the select is shown (it states the range); select 3 rem with a 2 px start padding (a bare 3 rem clipped "24 h" to "24", checked on a zoomed PNG); gaps and Expand padding take the small step.
  - Result, header heights read-mode (verify.mjs): 1440: 20 px for all three (one line); 1024: 20 px for all three; **1280: 38 px (two lines), not achieved.** At 1280 the content needs 290 (eps), 332 (top_sources) and 288 (severity) px against 281 available. Closing it would need truncating the title or dropping the Expand border; I did not, to keep information. Two lines are the same cost as before UI-3.3b but the pill is no longer a third line.
  - Rejected variants (each measured): window label hidden only (fits eps and severity at 1440, top_sources 38 px); borderless Expand (fits, loses the button affordance).
- Expanded tables: top sources shows 50 of 50 (card 5), fleet classes 11 of 11 (card 8), catch-all senders and WAF sources every row of their reads. Recent anomalies and offenses: their reads ask `limit=5` (unchanged), so expanded shows the same 5 rows and says "Showing 5 of 18. The widget reads the latest 5; the Anomalies page lists the rest." (the read's bound is 5; the request was not raised). Dialog header stays fixed while the body scrolls (verified at all three viewports, `headAfter` = `headTop`), no page overflow.
- "at least" on every Mean EPS cell in the expanded top-sources table: it comes from the fixture (partial coverage, "Partial: 21,525 of 21,600 intervals"), through the existing per-class completeness logic, which I did not change.
- Tests: card shows the card count, expanded shows all rows (top sources 5 to 15, fleet classes 8 to 10, catch-all 5 to 9, WAF 6 to 9); keys asked of the data hook are identical expanded and not; "n of N" wording; pill moves to the body in a narrow panel only, controls stay in the head.
- Break-its (each restored, diff checked): expanded still slices (`topSources(data, beh.data)`) -> 2 failures; request limit grows (`activeAnomalies(expanded ? 50 : ...)`) -> 2 failures; pill back in the title row (`!strip` removed) -> 1 failure.

## 3. Gates
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: exit 0
- `npx vitest run --no-cache`: `Test Files  161 passed (161)` / `Tests  2310 passed (2310)`
- `npm run build`: `✓ Compiled successfully in 5.6s`, `✓ Generating static pages (25/25)`
- `verify.mjs` (exit 0, density summary in `/home/efe/Documents/QRadar-Dash-work/ui33b/logs/density-summary.txt`):
```
geometryDeltaPx {'open': 0.75, 'close': 0}
1440x900  eps_by_class read 20 / customize 20; top_sources 20 / 20; anomaly_severity 20 / 20
          customize-taller: []  added overflow: []  read overflow: []
1280x800  eps_by_class read 38 / customize 20; top_sources 38 / 20; anomaly_severity 38 / 20
          customize-taller: []  added overflow: []  read overflow: []
```
(verify.mjs measures 1440 and 1280 only; 1024 header heights were 20 px in my measure script.)

## 4. PNGs
`/home/efe/Documents/QRadar-Dash-work/ui33b/png/`: `home-read-{1440x900,1280x800,1024x768}.png`, `home-read-lower-*.png`, `expand-top-sources-*.png` (+`-scrolled-`), `expand-fleet-classes-{1440x900,1280x800}.png` (+scrolled), `expand-recent-anomalies-*.png` (+scrolled), `expand-recent-offenses-*.png` (+scrolled), plus customize/arrange/drag shots from verify.mjs.

## 5. Open points
- 1280 title rows are still two lines (see above); needs a product decision (truncate titles, or widen the three panels in the default layout, which is outside the allowed files).
- Expanded recent anomalies/offenses cannot show more than the read's 5 rows without raising the limit, which the spec forbids.
- The fleet-classes expanded shot is only available at 1440 and 1280 (the widget is added in customize mode, which shots.mjs skips at 1024).
- No fixture edits beyond the inherited enlarged copy in the scratch dir. All processes I started were stopped by PID.

## Round 2 (supersedes the title-row choice in section 2)
- Reverted the squeeze: the select is the normal compact select (3.5 rem), Expand and the link keep their normal padding and gaps; `--dash-select-w-tight` is removed from `tokens.css` (grep: 0 uses). Kept: the pill on the first body line in narrow panels, and the title's "24 h" hidden while the select is shown.
- The controls (select, Expand, link) sit in one `.tw-tools` wrapper that wraps as a unit, right-aligned, to a second line. New test pins the one-wrapper structure; the pill break-it still fails 1 test (re-run).
- Header heights, read mode (own measure script at 1440/1280/1024, `verify.mjs` at 1440/1280 agree):
  - 1440: `chart.eps_by_class` 20 px (one line; pill on the first body line); `table.top_sources` 38 px (two lines); `chart.anomaly_severity` 38 px (two lines).
  - 1280: all three 38 px (two lines; eps_by_class also has the pill line).
  - 1024: `chart.eps_by_class` 20 px; `table.top_sources` 38 px; `chart.anomaly_severity` 38 px.
  - Customize mode is 20 px for all, never taller than read mode; no added overflow; geometry delta open 0.75 px / close 0.
  - So: wrapping panels are top_sources and anomaly_severity at 1440, 1024 and 1280, and eps_by_class at 1280 only. This is the accepted honest result.
- Expand hit target: 60.4 x 14 CSS px, shorter than 24 px (WCAG 2.5.8 point; not changed, outside allowed files).
- Gates: `npm run lint` `✔ No ESLint warnings or errors`; `npm run typecheck` exit 0; `npx vitest run --no-cache` `Test Files  161 passed (161)`, `Tests  2311 passed (2311)`; `npm run build` `✓ Compiled successfully in 6.3s`, `✓ Generating static pages (25/25)`.
- PNGs re-captured in `/home/efe/Documents/QRadar-Dash-work/ui33b/png/` (all three viewports, read upper/lower, expanded tables). 300% crops (captured directly at 3x scale, not via magick): `png/crops/{eps_by_class,top_sources,anomaly_severity}-{1440,1280}.png`. I looked at them: the select shows "24 h" clear of the chevron, normal Expand and link spacing, wrapped group right-aligned with the same spacing.
- Processes stopped by PID; nothing under /tmp.
