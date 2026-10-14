# Task UI-3.3 — per-widget range, the configure drawer and the expand dialog

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**. Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative),
`frontend/src/app/tokens.css`, and the widget system as built: `frontend/src/widgets/*`
(`types.ts`, `registry.ts`, `DashboardView.tsx`, `Grid.tsx` and `layout.ts` from UI-3.2,
`Catalog.tsx`), `frontend/src/components/home/WidgetFrame.tsx`, and the reports
`docs/tasks/UI-3.1.report.md`, `UI-3.2.report.md`, `UI-3.5a.report.md`. Build on them; do not
change their contracts except where this task says so.

## Binding design

`docs/UI-REDESIGN-PLAN.md` UR.5.1 (`WidgetDef`: `range`, `config`, `defaultConfig`), UR.5.3 (the
Configure, Per-widget time range and Expand rows), UR.5.4 (`WidgetFrame` owns the chrome: title,
range, expand, configure, drill-down link).

## What exists, and the gap

`WidgetDef.range`, `config` and `LayoutWidget.range`/`config` exist, but every registry entry has
`range: null` and `config: {}`, and the `live()` helper loads components that take **no props**:
nothing reaches a widget. The fleet EPS chart has its own 1 h / 24 h / 7 d presets in its title
row, held in component state. There is no configure control and no expand.

## Build

1. **Props reach the widget.** A placed widget's component receives `WidgetProps`
   (`id`, `config`, `range`) plus `expanded: boolean`. The **effective range** is the widget's
   override, else the dashboard layout's `range`. Widgets that ignore a prop keep working.
2. **Per-widget range, for the widgets whose reads take a window.** `/fleet/summary` and
   `/fleet/eps-series` accept `window` ∈ `1h | 24h | 7d` (`backend/app/schemas/fleet.py`
   `FleetWindow`). Declare `range: ["1h", "24h", "7d"]` on `kpi.total_eps`, `chart.eps_by_class`,
   `table.top_sources`, `table.fleet_classes` and `chart.eps_fleet_total`, and make each read its
   effective range (one shared request per distinct URL still, through `useApiResource`). Every
   other widget keeps `range: null` and says nothing about range. **The title shows the effective
   range** in the existing window-text slot ("24 h"), for every range-declaring widget.
3. **One range control, generalized.** In read mode, a range-declaring widget shows the presets in
   its title row, as the fleet EPS chart does today; move that control into `WidgetFrame` so every
   range-declaring widget gets the same one (the EPS chart's own state is replaced by the widget's
   range; its tests keep their intent). Choosing a preset sets the widget's override in the
   in-memory layout. **Nothing is saved** (persistence is Phase 4); the session-only line stays.
4. **Configure drawer (customize mode only).** A per-widget "Configure" control opens a drawer,
   built like the catalog drawer, holding: the range choice (the presets plus "Follow the
   dashboard (24 h)", showing the dashboard's actual range) for range-declaring widgets, and one
   control per `config` key rendered generically from `ConfigSchema` (a select over the enumerated
   `options`, labelled; **never a text field**). A widget with neither range nor config shows no
   Configure control. Apply changes the in-memory layout; Cancel leaves it unchanged. No widget has
   `config` yet (UI-3.5b adds the first), so test the generic form with a test-only widget
   definition.
5. **Expand dialog.** `WidgetDef` gains `expandable: boolean` (charts and tables `true`, KPI and
   health cards `false`). An Expand control on an expandable widget opens a native `<dialog>`
   (`showModal`) with the same component, same config and range, `expanded: true`, sized to the
   viewport (no page overflow at 1024×768); `Esc` and a Close button close it; **focus returns to
   the Expand control**. Expanded, a table shows up to the bound its read already has (no new API
   read, no larger request than the API allows); a chart fills the dialog. The dialog's title is the
   widget's title with its effective range. Expand works in read mode and in customize mode.
6. **Chrome stays compact.** Range presets, Expand, Configure and the drill-down share the title
   row without wrapping it at the template sizes at 1440 and 1280; at narrow widths they collapse
   in a way you choose and justify (for example presets into a select). No icon fonts and no
   decorative icons (AGENTS.md §3a); text controls or the existing control styles.

## Tests

- Unit: effective range (override, else dashboard); the generic config form renders one select per
  key with exactly the schema's options and never a text input; Apply and Cancel; a widget without
  range or config has no Configure control.
- Component: each of the five range-declaring widgets requests its effective window (assert the
  URL) and titles it; the title-row presets change the override; the expand dialog opens with
  `expanded`, closes on `Esc` and on Close, and returns focus to its trigger; a non-expandable
  widget has no Expand control.
- Existing tests: the fleet EPS chart's preset tests may be moved to the generalized control
  **with the same assertions** (name each move in the report). The exact live-type list in
  `registry.test.ts` must not change. No other existing test is edited.
- Break at least six guards on purpose (for example: the override ignored; a text input in the
  config form; focus not returned; the window not sent; Cancel applying; a non-expandable widget
  offering Expand), confirm a test fails each time, restore, and list them.

## Screenshots (required)

A fixture API outside the repository (start from the fixture the task prompt names), ports from
the prompt. At 1440×900, 1280×800 and 1024×768: the Home in read mode with one widget on a
non-default range; customize mode with the configure drawer open on a range widget; the expand
dialog on the fleet EPS chart and on a table. Look at every PNG yourself and fix what looks wrong
before reporting.

## Allowed files

- `frontend/src/widgets/*` (+ tests)
- `frontend/src/components/home/WidgetFrame.tsx` and the five range widgets in
  `frontend/src/components/home/` and `frontend/src/components/catalog/` (+ tests, additions only
  except the moves allowed above)
- `frontend/src/lib/api.ts` (`homeReads` window parameters only)
- `frontend/src/app/tokens.css` (new tokens only)
- `docs/tasks/UI-3.3.report.md` (new)

No new dependency. Never `pkill -f` or `killall`; stop only the processes you started, by PID.

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into
`docs/tasks/UI-3.3.report.md` (AGENTS.md §6 format), with the PNG list. Do not commit.

## Amendment (2026-10-14 ~22:00): after UI-3.2, fixture, files

- **UI-3.2 is merged.** Customize mode has **one** `Edit` disclosure per widget (accessible name
  `Arrange [widget]`). It holds the Move and Resize steppers and `Remove widget`, and it replaced
  the separate Arrange and Remove controls. The reason: two controls crowded the title row, and
  at 1280 the KPI bodies overflowed. Keep that rule:
  - in customize mode, **Configure goes inside that disclosure** (or the disclosure opens the
    drawer), never as a second title-row control;
  - **re-run UI-3.2's density measurement** (`verify.mjs`: title-row height and body overflow,
    read vs customize, every template widget, at 1440 and 1280) **with the range presets and
    Expand added in read mode too**;
  - quote its output: no added overflow and no taller title row in customize, and in read mode
    the KPI cards must not overflow at 1280.
- **Fix while you are there:** UI-3.2's open Edit panel leaves the top edge of the KPI's big number
  visible above it (`/home/efe/Documents/QRadar-Dash-work/ui32/png/arrange-open-1440x900.png`).
  Position the panel so it covers the body cleanly.
- **Fixture:** copy `/home/efe/Documents/QRadar-Dash-work/ui32/fixture/` (Home and catalog
  routes, pinned clock) to `/home/efe/Documents/QRadar-Dash-work/ui33/fixture/`. Never edit the
  original. Add `window` handling to the fleet routes so each preset returns distinguishable data.
- **Ports:** Next 3133, fixture 8133, Chrome debugging 9133.
- **Everything persistent goes under `/home/efe/Documents/QRadar-Dash-work/ui33/`, never `/tmp`:**
  scripts, logs, PNGs (`png/`). Start the verification script from
  `/home/efe/Documents/QRadar-Dash-work/ui32/verify.mjs`.
- `frontend/src/components/home/WidgetFrame.test.tsx` and `frontend/src/widgets/dashboard.css`
  are allowed too; existing-test edits are limited as in Tests.
- This worktree has its own `node_modules` (`npm ci` ran): install nothing.
