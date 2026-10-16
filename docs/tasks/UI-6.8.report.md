# Task UI-6.8 — empty severity window, and the folded-class line

## Files changed

- `frontend/src/components/home/SeverityChart.tsx` — a complete read with nothing counted in the last 24 h renders one sentence and no plot. A capped list, or a total that contradicts the rows, does not use that sentence.
- `frontend/src/components/home/SeverityChart.test.tsx` — pins the empty page, the complete page whose rows all fall outside the window, the capped list, the contradictory total, and the HTTP error and forbidden states.
- `frontend/src/components/home/charts.css` — the class list and the folded-class line wrap inside the column. `th` in `globals.css` is `white-space: nowrap`; these two rules override that.
- `frontend/src/components/home/EpsByClass.test.tsx` — the folded-class span keeps `hc-rest` and the full list on its `title`, and the stylesheet contract requires `white-space: normal` plus `overflow-wrap: anywhere`.
- `frontend/src/app/page.test.tsx` — one assertion updated to the new sentence. This file is outside the task's allowed list. The old regex matched copy that this task replaces, so the suite could not go green without it. The assertion is stricter (exact sentence), not weaker. `EpsByClass.tsx` was not edited.

## What I did

The backend empty window is `GET /anomalies?since&until`, which filters `detected_at` and returns `PagedAnomalies` with `items: []` and `total: 0`. There is no separate "not measured" field on that page. An HTTP error or a missing permission stays in `WidgetFrame` and never reaches the chart body. Truncation is `total > items.length`, which `severityPerHour` already marks as a lower bound.

The empty axes in the review PNG are a second case. The fixture clock is `2026-10-14T18:00:00Z` and the fixture does not apply `since`/`until`. On 2026-10-16 the widget's `lastHours(Date.now(), 24)` is about `2026-10-15T12:00:00Z` to `2026-10-16T12:00:00Z`, so every returned row is `outsideRange`, every hour total is 0, and `items` is not empty. The old empty branch only ran when `items.length === 0 && total === 0`, so the chart still drew.

`emptyWindow` sums the hour totals. With a valid hour span and a count of 0:

- `lowerBound` (the list was capped): "None of the returned anomalies fall in this window. The list was capped, so this is not a measured zero." No plot. The header badge still says Truncated.
- `total === items.length`: "No anomaly opened in the last 24 h." The `24 h` is `WINDOW_LABEL`, the same span the header and `lastHours` use. No plot. This covers the empty page and a complete page whose rows all fall outside the window.
- otherwise (the total contradicts the rows): "The list's total does not match the rows returned, so this window is not counted." No plot.

A count above 0 still draws the columns, including a truncated list that has some rows inside the window.

The folded-class clip is inherited `white-space: nowrap` on `th`. `overflow-wrap` does not wrap under `nowrap`, so the span's ink (`Proxy, VPN, Network, Other, IPS, WAF, DNS`, scroll width 247) ran past the cell. `.hc-list tbody th` and `.hc-rest` now set `white-space: normal` and `overflow-wrap: anywhere`. `.hc-rest` also has `max-width: 100%`. The existing `title` still holds `Other classes: …`. The donut was left beside the list; raising the list's minimum width would drop it onto its own row at the template's 4-column width.

## Break-it

Each new guard was broken on purpose, the matching test failed, and the source was restored before the gates below.

1. Empty sentence reverted to "No anomaly was detected in the last 24 hours." `SeverityChart.test.tsx -t empty` failed twice (empty page, and every row outside the window). Both looked for "No anomaly opened in the last 24 h."
2. Empty branch also mounted `Columns`. "says a complete empty list is empty, and draws no chart" failed because `echarts.init` ran.
3. Empty sentence rendered as a sibling above `WidgetFrame`. The forbidden case failed because the document contained "No anomaly opened".
4. `white-space: normal` removed from `.hc-rest` only. "wraps the folded-class line and keeps the full list on its title" failed: the rule no longer contained that declaration.

## Screenshots

Capture script (outside the repo): `/home/efe/Documents/QRadar-Dash-work/ui68/capture.mjs`. Fixture is the ui66 dashboard server copied to `…/ui68/fixture-dash-server.py` (port 8168, default scene). Next on 3168, Chrome CDP on 9168. Page overflow was 0 at all three viewports (`scrollWidth === clientWidth`).

Before, Morning triage, both widgets. At 1024×768 the severity widget drew an empty plot: y label "anomalies per hour", x ticks 12:00, 18:00, 00:00, 06:00 UTC, no bars, and no sentence. The Other classes line was cut at the card edge as "Proxy, VPN, Network, Other, IPS, WAF, D". Measured `.hc-rest`: `white-space: nowrap`, scroll width 247 against a client width of 186, not past the widget's border box (the ink overflowed the span).

- `/home/efe/Documents/QRadar-Dash-work/ui68/png/before/before-dashboard-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/ui68/png/before/before-dashboard-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/ui68/png/before/before-dashboard-1024x768.png`

After, same dashboard. The severity widget says "No anomaly opened in the last 24 h." and has no chart (`hasChart` false at all three widths). The folded line's computed style is `white-space: normal`, `overflow-wrap: anywhere`, and `scrollWidth === clientWidth`. At 1440 the line is one row, "Proxy, VPN, Network, Other, IPS, WAF, DNS", inside the card. At 1280 it wraps (box about 110×45) and the same seven names are in the element. At 1024×768 it wraps on two lines, "Proxy, VPN, Network," then "Other, IPS, WAF, DNS", inside the card, with the donut still beside the list.

- `/home/efe/Documents/QRadar-Dash-work/ui68/png/after/after-dashboard-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/ui68/png/after/after-dashboard-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/ui68/png/after/after-dashboard-1024x768.png`

Narrowest editor width (registry `THIRD`, `minW` 3 of 12). 1024 is a 6-column grid, so the template is not squeezed there. On the 12-column grid the EPS item was forced to 3 columns (277 px at 1440, 237 px at 1280). The folded line still has `scrollWidth === clientWidth` (no horizontal clip). At 277 px the line is about 72×60; at 237 px the class column is about 32 px and the line is about 32×135, so `overflow-wrap: anywhere` can break a name. The 12-column body scrolls (`overflow: auto` in `dashboard.css`), and the `title` still holds the full list. Extras, not a substitute for the three viewports:

- `/home/efe/Documents/QRadar-Dash-work/ui68/png/before/before-eps-minw-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/ui68/png/before/before-eps-minw-1280x800.png`
- `/home/efe/Documents/QRadar-Dash-work/ui68/png/after/after-eps-minw-1440x900.png`
- `/home/efe/Documents/QRadar-Dash-work/ui68/png/after/after-eps-minw-1280x800.png`

## Gates

From `frontend/`, Node on `PATH` from `~/.local/node/bin`. All four exited 0.

`npm run lint`

```
> qradar-observability-frontend@0.1.0 lint
> next lint
✔ No ESLint warnings or errors
```

`npm run typecheck`

```
> qradar-observability-frontend@0.1.0 typecheck
> tsc --noEmit
```

`npx vitest run --no-cache`

```
 Test Files  263 passed (263)
      Tests  2998 passed (2998)
   Start at  14:50:39
   Duration  64.25s (transform 61.48s, setup 253.78s, import 143.84s, tests 370.99s, environment 504.78s)
```

`npm run build` (with `NEXT_PUBLIC_API_BASE_URL` and `INTERNAL_API_BASE_URL` set to `http://127.0.0.1:8168/api/v1`, so this same build served the after screenshots):

```
> qradar-observability-frontend@0.1.0 build
> next build
✓ Compiled successfully in 5.2s
✓ Generating static pages (26/26)
```

No pytest. This task is frontend only. `ruff format` was not run.

## Open points

- `lastHours` always returns a valid span, so `emptyWindow` returning null when `hours.length === 0` is unreachable from this widget. No sentence was added for that path.
- `page.test.tsx` is the extra file named above.
- At a 3-of-12 squeeze on a 1280-wide 12-column grid, the class column is about 32 px. Names can break mid-word there. They are not clipped by horizontal overflow, the body scrolls, and the title has the full list. The three review viewports show the full list without scrolling.
- `globals.css` still sets `th { white-space: nowrap }`. It was not edited; the override is local to the class list.
