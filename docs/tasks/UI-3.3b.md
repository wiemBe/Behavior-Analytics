# Task UI-3.3b — expanded tables show more rows; the 4-column title rows

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md`, `frontend/src/app/tokens.css`, `docs/tasks/UI-3.3.md` and
`docs/tasks/UI-3.3.report.md` (merged). This finishes two points UI-3.3 could not do, because the
files were outside its allowed list.

## 1. Expanded tables show up to their read's bound

UI-3.3 Build item 5: "Expanded, a table shows up to the bound its read already has (no new API
read, no larger request than the API allows)". Today an expanded table shows the same rows as the
card.
- `table.top_sources`: `lib/home/topSources.ts` slices to `TOP_N`. Give it a `limit` parameter
  (default `TOP_N`); `TopSources` passes no limit when `expanded`, so every source the read
  returned is shown.
- `table.fleet_classes`: `components/catalog/derive.ts` `classRows` caps at
  `FLEET_CLASS_ROWS = 8`. Same treatment.
- `table.recent_anomalies` and `table.recent_offenses`, and any other expandable table widget:
  - when `expanded`, show every row the existing read returned;
  - **never** raise the request's `limit` beyond what the read already asks;
  - if the card shows "Showing the latest 5 of N", the expanded view says how many it shows of how
    many there are.
- An expanded table with many rows scrolls inside the dialog body. The dialog header stays put,
  and the page itself does not overflow.

## 2. The title row of the three 4-column panels

At 1440 and 1280, `chart.eps_by_class`, `table.top_sources` and `chart.anomaly_severity` cannot fit
title, range control, Expand and the drill-down link on one line. Their controls drop to a
second line (UI-3.3 measured +18 px), and `chart.eps_by_class` also carries its Partial pill, so
its header reads as three lines. Reduce this without dropping information:
- Measure the options in the browser first. For example: the status pill moves from the title row
  to the first line of the body; the range control in those panels is the compact select that the
  KPI cards use; Expand sits next to the drill-down link with less spacing.
- Pick the one that brings each of the three title rows to at most one line plus the pill, at 1440
  and 1280. Justify the choice in the report.
- Keep UI-3.2 and UI-3.3's density rules: run `verify.mjs` (start from a copy of
  `/home/efe/Documents/QRadar-Dash-work/ui33/verify.mjs`) and quote its output. No added
  overflow, and no customize-mode title row taller than read mode.

## 3. Housekeeping

- Remove the stale `.hc-presets` and `.hc-preset` rules from `components/home/charts.css`. UI-3.3
  moved the presets into `WidgetFrame`. First prove nothing uses them (grep).

## Tests

- Each expandable table: when `expanded`, it renders more rows than the card, up to what the read
  returned, and the request URL and its `limit` are unchanged.
- The "n of N" wording when expanded.
- **Existing-test edits:** none, unless a test pins the exact row count of the card. That count
  must stay as it is in the card; say so if you meet one.
- **Break-its:**
  - expanded still slices to the card size;
  - the request's limit grows when expanded;
  - the pill wraps back into the title row.

  Confirm a test fails each time, then restore.

## Screenshots

Copy the fixture at `/home/efe/Documents/QRadar-Dash-work/ui33/fixture/` to
`/home/efe/Documents/QRadar-Dash-work/ui33b/fixture/`. Never edit the original. Make it return more
sources and classes than the card shows.
- Ports: 3134/8134/9134.
- Capture at 1440×900, 1280×800 and 1024×768:
  - the Home in read mode (the three panels' headers);
  - the expanded top-sources table;
  - the expanded fleet-classes table;
  - the expanded recent-anomalies table.
- PNGs go to `/home/efe/Documents/QRadar-Dash-work/ui33b/png/`. Nothing goes under `/tmp`.
- Look at every PNG.

## Allowed files

- `frontend/src/lib/home/topSources.ts`
- `frontend/src/components/catalog/derive.ts`
- the expandable table widgets in `frontend/src/components/home/` and
  `frontend/src/components/catalog/`
- `frontend/src/components/home/WidgetFrame.tsx`
- `frontend/src/components/home/charts.css`
- `frontend/src/widgets/*`
- new tests beside each
- `frontend/src/app/tokens.css` (new tokens only)
- `docs/tasks/UI-3.3b.report.md` (new)

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the PNG list and the `verify.mjs` output. Do not commit. Stop every
process you start, by PID. Never `pkill -f` or `killall`.
