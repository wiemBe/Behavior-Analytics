# UI-2.3 report: Home charts (fleet EPS, EPS by class, anomaly severity)

## 1. Files changed
- `frontend/src/components/home/EpsChart.tsx` (+ `EpsChart.test.tsx`), `EpsByClass.tsx` (+ test), `SeverityChart.tsx` (+ test): the three widgets. All use the shared `Widget` / `Gate` / `RefreshNote` from `WidgetParts.tsx` (no third frame) and the `.tw` panel from `tables.css`.
- `frontend/src/components/home/chartOptions.ts` (+ `chartOptions.test.ts`): the three ECharts options as pure functions of a model and a palette, plus the tooltip builders.
- `frontend/src/components/home/useEChart.ts` (new, not in the task's file list; it is the "create on mount, dispose on unmount, resize with the container" hook the three charts share, instead of three copies). Flagged here as the one file beyond the list.
- `frontend/src/components/home/charts.css` (new): all values are tokens.
- `frontend/src/lib/home/epsChart.ts` (+ test): fleet EPS model (gaps, lone steps, measured counts, qualifiers, summary text); `chartPalette.ts` (+ test): the extra tokens (categorical, severity, surface) and `var(--…)` helpers for SVG swatches; `qualifiers.ts` (+ new `qualifiers.test.ts`): `badgeText`; `severityPerHour.ts` (+ test): `lastHours`, `severityText`; `fixtures.ts`: `epsPoint`, `epsSeries` builders.
- `frontend/src/app/tokens.css`: `--home-chart-h` (152px), `--home-donut` (112px), `--home-list-min`, `--home-col-num`.
- Temporary route `frontend/src/app/preview-charts/` (page + css): created for capture and deleted; not in the diff, build passes without it.

## 2. What I did
- Each widget is a title row (title, window, one pill, drill-down link), the chart, and at most one row under it. Definitions are in tooltips, `title` and `sr-only` text, not paragraphs.
- **Fleet EPS** (`GET /fleet/eps-series?group_by=none`): one observed line, `connectNulls: false`, no marker on every point (a lone measured step between gaps is marked, as `VolumeChart` does). A null step is a shaded interval (`--chart-gap`); the tooltip on it says "Observed: not measured (n of m source-intervals measured)". The expected series is one dashed line named "Sum of per-source expected", present only if some step carries `expected_eps`, null where it is null; no band, no p05/p95 series. The key entry's `title` and the `sr-only` description say it is a sum, drawn only where every source has a reliable baseline, "not a range". Presets 1 h / 24 h / 7 d (buttons, `aria-pressed`) re-read for that window. y axis "EPS", x axis "UTC" (labels HH:mm, day boundary "14 Oct"). Measured counts come from the steps, not the response's coverage field. States: loading, forbidden (`read:fleet`), error, refresh-failed (data stays), NOT_COLLECTED (no step measured: reason replaces the chart, no empty axes), PARTIAL ("Partial: 271 of 288 steps collected"). Title row link "Fleet" -> `/fleet` in every state.
- **EPS by source class** (`GET /fleet/summary?window=24h`): `epsByClass.ts` already caps at five; the option also caps at five (own guard, own test). 2 px gap in `--surface`, `--chart-cat-1..4` + `--chart-other`, total EPS in the hole. The donut is `aria-hidden`; the table beside it (class, EPS, share, total in the caption) is the text carrier. "Other classes" names its contents on a sub-line. Classes with no mean are counted ("1 class has no complete bucket and is not in the total"), never added as zero. Slice or row -> `/fleet/sources?class=X`; the aggregate has no single class so it goes to `/fleet`. INSUFFICIENT_DATA (no class has a mean): pill + reason, no donut. PARTIAL pill with the interval count.
- **Anomalies by severity** (`GET /anomalies?since&until&limit=200`, last 24 UTC hours, range moves only when the hour does): stacked columns, one series per severity in the fixed order CRITICAL, HIGH, MEDIUM, LOW, INFO (unknown severities after, by name), `--sev-*` through `readHomePalette`; key with the words (SVG swatch, `fill="var(--sev-…)"`, so no CSS is keyed to a severity name); y axis "anomalies per hour". TRUNCATED: pill "Truncated: first 200 of 345", the axis reads "anomalies per hour, at least", the tooltip says "At least n", the sr-only text says "lower bound". A complete empty list says so and draws no chart. A segment click -> `/anomalies?since&until&severity` (hour bounds + the series' severity); the title link -> `/anomalies?since&until`.
- **Anomalies page supports the parameters.** `app/anomalies/page.tsx` reads `since`, `until` and `severity` from the URL, so the links are real, no fallback to bare `/anomalies`.
- No inline styles; the only literals in the option objects are the 2 px slice gap, grid margins and font-independent sizes inside ECharts options (colours are all from the palette; a test asserts no `#hex`/`rgb(` appears in any option).
- Animation: `animation: !prefers-reduced-motion`, as `VolumeChart`.

## 3. Gates (from `frontend/`)
- `npm run lint`: `✔ No ESLint warnings or errors`
- `npm run typecheck`: `tsc --noEmit`, no output
- `npm test`: ` Test Files  134 passed (134)` / `      Tests  1958 passed (1958)`
- `npm run build`: ` ✓ Compiled successfully in 8.1s`, ` ✓ Generating static pages (25/25)` (run after the preview route was deleted)

### Break-it checks (each made tests fail; code restored after each; focused test runs)
1. `connectNulls: true` on the observed series: chartOptions "hands the unmeasured step over as null and does not connect" fails.
2. Unmeasured step turned into 0 in `epsChartModel`: 6 `epsChartModel` tests fail.
3. Expected series always drawn: chartOptions "has no expected series at all where no step carries one" and EpsChart "has no expected line, no key entry..." fail.
4. Expected line `solid` instead of `dashed`: chartOptions "draws the expected sum as one dashed, named line" fails.
5. `MAX_ENTRIES` raised (no five-slice fold): 5 `epsByClass` tests and EpsByClass "never draws more than five slices" fail. Then, separately, `slice(0, MAX_SLICES)` removed from `buildClassOption`: the new "caps what it is handed at five" test fails.
6. Severity stack order left as received: chartOptions "stacks the severities in the fixed rank order" fails.
7. `badgeText` ignores TRUNCATED: SeverityChart "shows TRUNCATED..." and 2 `badgeText` tests fail.
8. Chart not disposed on unmount: EpsChart "disposes the chart on unmount" fails.
9. y axis without "at least" on a capped list: chartOptions and SeverityChart truncation tests fail.
10. Class link `?class=` changed: EpsByClass link and slice-click tests fail.

## 4. Screenshots and measured heights
Fixture API on 8123, Next on 3123 (production build), own Chrome profile; scripts in `/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/`, outside the repo. The preview page placed the widgets in a 12-column grid (EPS 7, class 5, severity 7; one column below 1100 px), which is only my layout, not UI-2.5's. `document.scrollWidth == clientWidth` at all 18 captures.

Widget heights, px (Fleet EPS / EPS by class / Severity):
| scenario | 1440x900 | 1280x800 | 1024x768 |
|---|---|---|---|
| ok (null steps, expected line) | 262 / 293 / 250 | 262 / 293 / 250 | 262 / 265 / 250 |
| noexpected | 262 / 293 / 250 | 262 / 293 / 250 | 262 / 265 / 250 |
| notcollected (all null) | 152 / 122 / 250 | 152 / 122 / 250 | 152 / 122 / 250 |
| truncated (200 of 345) | 262 / 293 / 252 | 262 / 293 / 252 | 262 / 265 / 252 |
| forbidden (severity) | 262 / 293 / 88 | 262 / 293 / 109 | 262 / 265 / 88 |
| down (HTTP 502) | 170 / 151 / 130 | 170 / 151 / 130 | 170 / 130 / 130 |

PNGs (all 18; I looked at ok 1440 and 1280, noexpected 1024, notcollected 1280, truncated 1024, forbidden 1440, down 1280 directly, and the rest through the overflow and height measurements):
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/ok/charts-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/ok/charts-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/ok/charts-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/notcollected/charts-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/notcollected/charts-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/notcollected/charts-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/noexpected/charts-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/noexpected/charts-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/noexpected/charts-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/truncated/charts-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/truncated/charts-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/truncated/charts-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/forbidden/charts-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/forbidden/charts-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/forbidden/charts-1024x768.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/down/charts-1440x900.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/down/charts-1280x800.png
- /tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/png/down/charts-1024x768.png

## 5. Open points
- The widgets are not on a page; UI-2.5 composes `/`. The class widget needs about 367 px of inner width to keep the donut beside the list; below that the list wraps under the donut (`--home-list-min`), it does not overflow.
- The `EPS by class` and `Fleet EPS` pills are strings (the existing `Widget` takes `badge: string`), and the presets sit in the row under the chart because `Widget` has no slot for header controls. A header slot in `WidgetParts.tsx` (not my file) would let the presets sit beside the title.
- Chart canvases are not keyboard-operable; the links in the title row and the class list are the keyboard path to the same destinations. A severity column has no non-canvas link per hour.
- Severity range is the last 24 UTC hours only (no presets, none specified); the last column is the current partial hour.
- `EpsByClass` uses the 24 h window, matching the KPI card; the EPS chart's presets do not change it.
- jsdom cannot show layout; the geometry was verified only in headless Chrome.
