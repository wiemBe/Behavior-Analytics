# UI-3.3 report: per-widget range, configure drawer, expand dialog

## 1. Files changed

New (all under `frontend/src/`):
- `widgets/runtime.tsx`: `WidgetRuntimeContext` (range options, override, dashboard range, `setRange`, `onExpand`, `expanded`), `effectiveRange`, `useEffectiveRange`, `RANGE_TEXT`, `RangedProps`.
- `widgets/RangeControl.tsx`: the one range control (preset buttons, or a select).
- `widgets/Configure.tsx`: `ConfigureDrawer`, the generic `ConfigForm` (one select per `ConfigSchema` key), `hasConfigure`.
- `widgets/Expand.tsx`: `ExpandDialog` (native `<dialog>`, `showModal`).
- Tests: `widgets/runtime.test.ts`, `widgets/Configure.test.tsx`, `widgets/ranges.test.tsx`, `widgets/DashboardView.range.test.tsx`, `widgets/registryRange.test.ts`, `components/home/WidgetFrame.expand.test.tsx`.

Modified:
- `widgets/types.ts`: `WidgetProps.expanded`; `WidgetDef.expandable`.
- `widgets/registry.ts`: `range` on the five fleet widgets (`1h|24h|7d`), `expandable` (Chart and Table categories, live only), loaders typed to take props, descriptions no longer say "24 h".
- `widgets/DashboardView.tsx`: passes `range`/`config`/`expanded` to the component; owns the range override, the expand dialog, the configure drawer and the focus return.
- `widgets/Grid.tsx`: the one Edit disclosure also holds Configure and Expand (and Remove).
- `widgets/dashboard.css`, `app/tokens.css`: range control, Expand, drawer, dialog; Edit panel anchoring fix. New tokens only: `--dash-expand-max-w`, `--dash-expand-inset`, `--dash-select-w`.
- `components/home/WidgetFrame.tsx`: renders the range control and Expand in the title row (read mode), hides both in customize mode and in the dialog.
- `components/home/{EpsChart,KpiTotalEps,EpsByClass,TopSources}.tsx`, `components/catalog/FleetClasses.tsx`: read the effective range; the window is in the request, the title and the description.
- `components/home/EpsChart.test.tsx`: one test moved out (see 2).
- `lib/api.ts` was not touched: `homeReads.fleetSummary(window)` and `epsSeries(window, ...)` already take the window.
- `docs/tasks/UI-3.3.report.md` (this file).

Test moved: "offers 1 h, 24 h and 7 d, and a press re-reads for that window" left `EpsChart.test.tsx` and is in `widgets/ranges.test.tsx` with the same assertions (the control now belongs to `WidgetFrame`, so it is rendered under a dashboard-like provider). No other existing test was edited. `registry.test.ts` is unchanged and passes, so the live-type list is unchanged.

## 2. What was done

1. **Props reach the widget.** `PlacedWidget` passes `id`, `config`, `range` (the override) and `expanded`. A widget reads the effective range (override, else the dashboard's, else 24 h) with `useEffectiveRange`. Widgets that ignore the props keep working; a bare widget (no dashboard) follows 24 h, shows no range control and no Expand.
2. **Range on five widgets**: `kpi.total_eps`, `chart.eps_by_class`, `table.top_sources`, `table.fleet_classes`, `chart.eps_fleet_total`. Each request carries the window (still one request per distinct URL through `useApiResource`). The title shows the range in the window-text slot (KPI: "Total EPS, 7 d", as before). Every other widget has `range: null`.
3. **One range control**, in `WidgetFrame`: preset buttons (`role=group` "Window", `aria-pressed`) in a panel; a select at KPI density, and in a panel narrower than 28 rem (a container query hides the one that does not fit, so the hidden one is out of the accessibility tree). Choosing sets the override in the in-memory layout. Nothing is saved; the session-only line stays. Justification: a KPI card at 1280 has about 200 px of title row.
4. **Configure drawer**: customize mode only, inside the Edit disclosure (title row keeps one control). It holds "Time range" (Follow the dashboard (24 h), 1 h, 24 h, 7 d) for range widgets and one labelled select per config key (never a text field). Apply sets range and config in memory; Cancel and Esc change nothing. A widget with neither range nor config has no Configure. No widget has config yet, so the form is tested with a test-only definition.
5. **Expand dialog**: `expandable` on Chart and Table widgets (KPI, Health, Admin false). Native `<dialog>` + `showModal`, `Esc` and Close, focus returns to the control that opened it (captured from the click, so it also works from the Edit panel). Title is the widget's title plus its effective range; the range control is in the dialog header. Works in read and in customize mode (from the Edit disclosure). The dialog is `calc(100vw - 2 x 24 px)` by `calc(100vh - 2 x 24 px)`: no page overflow at 1024 x 768 (asserted in the browser run).
6. **Density** (amendment): Configure and Expand live in the Edit disclosure; customize mode never adds a title-row control. Measurement below.
7. **Edit panel fix**: the panel now hangs from the bottom edge of the title row and reaches the card edges, so it covers the body, and the KPI number's top edge no longer shows (see `edit-panel-kpi-1440x900.png` against UI-3.2's `arrange-open-1440x900.png`). Side effect fixed: at 1280 the step buttons ("Narrow") were clipped in a KPI card; the browser run now asserts none is.

### Density measurement (`verify.mjs`, read mode, in-run baseline = the new controls hidden)

Quoted from `/home/efe/Documents/QRadar-Dash-work/ui33/logs/ui33-output.json`. Title-row height in px (UI-3.2 state -> now); body overflow includes any scrolling region inside the body.

```
1440x900
  kpi.total_eps          18   -> 18    overflow False -> False
  kpi.log_sources        18   -> 18    overflow False -> False
  kpi.offenses           32.5 -> 32.5  overflow False -> False
  kpi.anomalies          18   -> 18    overflow False -> False
  chart.eps_fleet_total  20*  -> 25    overflow False -> False   (*UI-3.2 recorded 28 with its own presets)
  chart.eps_by_class     48   -> 66    overflow False -> False   GREW
  table.top_sources      20   -> 38    overflow False -> False   GREW
  chart.anomaly_severity 20   -> 38    overflow False -> False   GREW
  health.log_sources     20   -> 20    ; table.recent_anomalies 24 -> 24 ; table.recent_offenses 20 -> 20
1280x800
  kpi.total_eps          18   -> 18    overflow False -> False
  kpi.log_sources        18   -> 18    overflow False -> False
  kpi.offenses           32.5 -> 32.5  overflow False -> False
  kpi.anomalies          32.5 -> 32.5  overflow False -> False
  chart.eps_fleet_total  20*  -> 25    overflow False -> False
  chart.eps_by_class     65   -> 66    overflow False -> False
  table.top_sources      20   -> 38    overflow False -> False   GREW
  chart.anomaly_severity 20   -> 38    overflow False -> False   GREW
  health.log_sources 20 -> 20 ; table.recent_anomalies 24 -> 24 ; table.recent_offenses 20 -> 20
```

- KPI cards: no growth and no overflow at 1440 and 1280 in read mode (the script throws otherwise).
- Customize vs read: no title row taller and no added overflow for any widget at 1440 and 1280 (`assertCustomizeDensity` passes). Customize title rows at 1440: KPIs 18, panels 20, `chart.eps_by_class` 48, `table.recent_anomalies` 24.
- **Deviation from item 6 of the task:** three 4-column panels (`chart.eps_by_class`, `table.top_sources`, `chart.anomaly_severity`) cannot hold title, range, Expand and the drill-down link on one line at 1440 or 1280 (about 348 and 293 px of content; the controls and the title need about 360). The controls drop to a second line as one group, so those title rows are one line taller (+18 px). No body overflow results. The only way to avoid it is a different layout (wider widgets, or removing a control); I did not change the template. (An intermediate 1 px scrollbar in `table.top_sources` at 1280 was found by the inner-overflow measurement and fixed by tightening the wrapped row.)

### Break-it results (`logs/breakit.txt`; each mutation reverted and the file restored)

All ten were caught by the new tests:
1. override ignored (`effectiveRange` returns the dashboard's range): `runtime.test`, `ranges.test`.
2. a text input added to the config form: `Configure.test` (several).
3. focus not returned after close: `DashboardView.range.test` (expand and drawer).
4. window not sent (fleet EPS chart reads `"24h"`): `ranges.test`.
5. Cancel applies the choice: `Configure.test`, `DashboardView.range.test`.
6. a non-expandable widget offers Expand: `registryRange.test`.
7. `expanded` not passed to the dialog copy: `DashboardView.range.test`.
8. Esc does not close the expand dialog: `DashboardView.range.test`.
9. KPI gets preset buttons instead of the select: `WidgetFrame.expand.test`.
10. Configure offered for a widget with nothing to configure: `Configure.test`, `DashboardView.range.test`.

## 3. Gates (from `frontend/`)

- `npm run lint`: `No ESLint warnings or errors`
- `npm run typecheck`: clean (`tsc --noEmit`, no output)
- `npx vitest run --no-cache`: `Test Files  159 passed (159)`, `Tests  2281 passed (2281)`
- `npm run build`: `Compiled successfully in 6.5s`, `First Load JS shared by all 103 kB`
- Browser run (`/home/efe/Documents/QRadar-Dash-work/ui33/verify.mjs`, Chrome headless, fixture API 8133, Next 3133, Chrome 9133): passes. It re-runs UI-3.2's checks (pointer drag and resize, keyboard move and resize, no hydration warnings, no page overflow) and asserts: `window=7d` and `window=1h` requested, titles, expand dialog inside the viewport and no page overflow at the three viewports, focus returned after Esc, Close and Cancel, Apply sets 1 h, no clipped step button. All processes were stopped by PID; ports 3133, 8133, 9133 are closed.

## 4. Screenshots (`/home/efe/Documents/QRadar-Dash-work/ui33/png/`)

Read mode, one widget on 7 d and the KPI on 1 h: `read-range-1440x900.png`, `read-range-1280x800.png`, `read-range-1024x768.png`; lower half: `read-lower-1440x900.png`, `read-lower-1280x800.png`, `read-lower-1024x768.png`.
Customize, Edit panel with Configure/Expand/Remove: `edit-panel-kpi-1440x900.png`, `edit-panel-kpi-1280x800.png`, `edit-panel-chart-1440x900.png`, `edit-panel-chart-1280x800.png`.
Configure drawer: `configure-1440x900.png`, `configure-1280x800.png`.
Expand, fleet EPS chart: `expand-eps-1440x900.png`, `expand-eps-1280x800.png`, `expand-eps-1024x768.png`. Expand, table: `expand-table-1440x900.png`, `expand-table-1280x800.png`, `expand-table-1024x768.png`. Expand from customize mode: `expand-customize-1440x900.png`, `expand-customize-1280x800.png`.
UI-3.2 regression shots still produced: `customize-*`, `mid-drag-placeholder-1440x900.png`, `home-after-drag-1440x900.png`, `home-after-resize-1440x900.png`, `read-1024x768.png`.

I looked at the read-range, read-lower, edit-panel, configure and expand PNGs at 1440 and 1280, and the read-range and expand-table PNGs at 1024, and fixed what looked wrong (Expand read as part of the link, so it is drawn like a preset; clipped step buttons at 1280; a scrollbar in the top-sources table at 1280).

## 5. Open points

- **The configure drawer at 1024 x 768 cannot be shown**: customize mode is offered at 12 columns only ("Widen the window to customize"), so there is no Configure at 1024. Read mode and Expand are captured there.
- **Expanded tables show no extra rows.** "Up to the bound the read already has" needs `lib/home/topSources.ts` (`TOP_N` slice in `topSources`) and `components/catalog/derive.ts` (`classRows`, `FLEET_CLASS_ROWS = 8`) to take a limit. Neither file is in this task's allowed list, so I did not touch them. The recent-anomalies, recent-offenses and catch-all widgets are also outside the allowed list and ignore `expanded`. Expanded tables do gain width (the State column unfolds); the five fleet widgets take `expanded` from the frame. A follow-up needs: `topSources(summary, behaviors, limit = TOP_N)` and `classRows(summary, limit = FLEET_CLASS_ROWS)`, then `TopSources`/`FleetClasses` pass `Infinity` when `expanded`.
- The 4-column title-row wrap above.
- `components/home/charts.css` still holds the now-unused `.hc-presets`/`.hc-preset` rules (not in the allowed list).
- `catalog/Catalog.tsx` still blocks a second copy of a widget (`ALREADY_PLACED`); its comment says UI-3.3 config makes duplicates meaningful, but no widget has config yet, so I left the guard for UI-3.5b.
- Fixture: `/home/efe/Documents/QRadar-Dash-work/ui33/fixture/` (copied, port 8133, CORS origin 3133); the fleet routes honor `window` with distinct data (1 h: 12 five-minute steps and 0.62x EPS; 24 h: 24 steps; 7 d: 28 six-hour steps and 1.18x). The original ui32 fixture is untouched.
- Nothing was committed.
