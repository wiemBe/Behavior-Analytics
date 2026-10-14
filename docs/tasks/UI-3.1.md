# Task UI-3.1 — the widget registry, the state union, the catalog, and the Home as a `DashboardView`

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**. Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative) and
`frontend/src/app/tokens.css`. The user approved the Phase 2 Home screenshots on 2026-10-14; this
task must not make the Home look worse.

## Binding design

`docs/UI-REDESIGN-PLAN.md` **UR.5** (the widget system): UR.5.1 (registry and catalog), UR.5.3
(interactions; only Add and Remove are in this task), UR.5.4 (widget states), UR.5.5 (the layout
JSON v1; nothing is saved in this task), UR.5.6 (permissions), UR.5.7 (the SOC Overview template).

**Task split ruling (orchestrator, 2026-10-14).** The plan's UI-3.1 (registry, frame, catalog) and
UI-3.4 (the Home as a `DashboardView`, in memory) are done together here, because neither can be
seen in a browser without the other. Dragging and resizing (`react-grid-layout`) is the next task
(UI-3.2); configure, expand and per-widget range are UI-3.3; the remaining catalog widgets are
UI-3.5. Nothing is saved before Phase 4.

## Build (all new code under `frontend/src/widgets/` unless named)

1. **Types** (`types.ts`): `WidgetDef<C>` exactly as UR.5.1 (type, title, description, category,
   requires, sources, status, blockedBy, size, range, config, defaultConfig, drillDown, Component),
   `WidgetProps<C>`, the `WidgetState<T>` union of UR.5.4, and the layout v1 types of UR.5.5
   (`{v: 1, range, widgets: [{id, type, x, y, w, h, range, config}]}`).
2. **Registry** (`registry.ts`). Register the Phase 2 components **as they are** (wrap, do not
   rewrite): `kpi.total_eps`, `kpi.log_sources`, `kpi.offenses`, `kpi.anomalies`,
   `chart.eps_fleet_total` (the Home's fleet EPS chart, built on B-2.1), `chart.eps_by_class`,
   `table.top_sources`, `chart.anomaly_severity`, `health.log_sources`, `table.recent_anomalies`,
   `table.recent_offenses`; all `live`, with their real `sources` and `requires`. Two `blocked`
   entries, shown disabled with the reason: `map.geography` ("the map widget is not built yet;
   geo collection is"), `ips.source_anomalies` ("the IPS analyst API is being built"). **List no
   other widget**: the catalog lists only what exists (UR.5.1: it does not tease). Components load
   with `next/dynamic` (`ssr: false`) as UR.5.1 says, under a text-only loading state.
3. **Registry test** (`registry.test.ts`): `type` matches `^[a-z0-9_.]{1,64}$` and is unique;
   every `sources` entry is a route the frontend client actually calls (read `src/lib/api.ts`'s
   source text and assert each path appears); every `requires` is a known permission: keep a
   `KNOWN_PERMISSIONS` list in `widgets/permissions.ts` and have the test read
   `../backend/app/security/rbac.py` and assert the list equals the values of its `PERM_*`
   constants, so the two cannot drift; a `blocked` widget has a `blockedBy` and is in no template;
   a `live` widget has none; every template widget is in the registry and `live`.
4. **State union in the frame.** `components/home/WidgetFrame.tsx` stays the one frame (do not
   move or fork it). Make it able to render every `WidgetState` kind of UR.5.4 (`loading`, `ok`,
   `empty`, `no_data`, `not_collected`, `stale`, `error`, `forbidden`), with the qualifiers
   (`INSUFFICIENT_DATA`, `PARTIAL`, `TRUNCATED`) on `ok` as today. Add the pure
   `deriveStale(asOf, cadenceSeconds | null, now)` of UR.5.4 (stale when `asOf` is older than twice
   the cadence; 2 h when the cadence is unknown) with tests. The Phase 2 widgets keep their current
   behaviour; wiring staleness into each widget is not required here (say which widgets could use
   it in the report).
5. **Template** (`templates.ts`): the SOC Overview template of UR.5.7 as a versioned object
   `{key: "soc_overview", version: 1, name, layout}`, with positions that reproduce the approved
   Phase 2 Home: the KPI row (4 × w3), fleet EPS w8 + class w4, top sources / severity / health
   3 × w4, recent anomalies / recent offenses 2 × w6.
6. **Geometry.** Fixed row heights, so the next task's `react-grid-layout` drops in with the same
   geometry: a widget box is `h × row + (h − 1) × gap` tall, from new tokens (`--dash-row`,
   `--dash-gap`). The widget body fills its box (the `fillBody` path); if content is ever taller
   it scrolls inside the body, **never clipped**, but the template must size every widget so that
   nothing scrolls at 1440×900 and 1280×800 with the ok fixture. A pure, tested
   `deriveLayout(lg, cols)` (UR.5.3) gives the 6- and 1-column layouts from the stored 12-column
   one. Columns follow the **container** width: 12 at ≥ 900 px (the threshold UI-2.5 settled so
   1280 keeps 12 columns), 6 from 600 to 899 px, 1 below.
7. **`DashboardView`** (`DashboardView.tsx`) renders a layout v1 through the registry. An unknown
   `type` renders the frame with "Unsupported widget" (UR.5.5), never crashes. A `blocked` type
   renders its reason. `app/page.tsx` renders the Home through `DashboardView` over the SOC
   Overview template; `HomeDashboard.tsx` keeps the page header and the data-age report and drops
   its fixed grid. **At 1440, 1280 and 1024 the Home must look like the approved Phase 2 Home**
   (same order and spans; the 1024 reflow as Phase 2's).
8. **Customize (session only).** A "Customize" toggle in the page header's actions. In customize
   mode: each widget shows a Remove control (keyboard reachable, named "Remove {title}"); an
   "Add widget" control opens the **catalog drawer** (`Catalog.tsx`): widgets by category, each
   with its description, the data it reads and the permission it needs; `blocked` ones disabled
   with the reason; widgets whose `requires` the caller lacks (from `GET /me` when readable, UR.5.6)
   disabled with the permission named. Adding places the widget at the first free spot at its
   default size. "Done" leaves customize mode. A line in customize mode says plainly that changes
   last for this session only (saving is not built yet). **Editing is offered only at the
   12-column width**; below it the toggle is replaced by "Widen the window to customize" (UR.5.3).
   The drawer is a real dialog (focus moves in, `Esc` closes, focus returns to the trigger).
9. **Permissions on placed widgets** keep today's forbidden state (UR.5.6).

## Tests (Vitest)

Registry test (3); `deriveStale`; `deriveLayout` (12 → 6 → 1, order preserved, no overlap, `x + w`
within the columns); `DashboardView` (renders the template's widgets in order, unknown type,
blocked type); the frame renders every `WidgetState` kind once; customize mode (add from the
catalog at the first free spot, remove, disabled blocked and forbidden entries with their reasons,
the drawer's focus handling, the session-only line, no customize below 12 columns); the Home page
test still passes (adapt only selectors that the new structure changes, keeping every assertion's
intent; list each edit). Break at least six guards on purpose, confirm a test fails, restore, list.

## Screenshots (required)

Against a deterministic fixture API outside the repository (start from
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/`
`fixture.mjs` and `capture.py`; copy them to
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/a16a3a57-0bb1-488e-a4d5-dadbc9e16dfd/scratchpad/ui31/`;
ports **3131** for Next and **8131** for the fixture API; Chrome debugging port 9131):
- `/` at 1440×900, 1280×800, 1024×768 (ok fixture), first screen and full page: compare each with
  the Phase 2 PNGs in `/home/efe/Documents/QRadar-Dash-screens/phase2/` and report any difference;
- customize mode at 1440 and 1280; the catalog drawer open at 1440 and 1280 (with one blocked and
  one forbidden entry visible); the Home after removing one widget and adding it back; 1024 showing
  "Widen the window to customize".
Look at every PNG yourself and list them with what each shows. Keep the geometry audit (page
overflow, widget heights, nothing scrolling inside a widget at 1440/1280).

## Allowed files

- `frontend/src/widgets/*` (new), `frontend/src/app/page.tsx`, `frontend/src/app/page.test.tsx`
  (selector adaptations only, listed)
- `frontend/src/components/home/WidgetFrame.tsx`, `WidgetFrame.test.tsx`, `HomeDashboard.tsx`,
  `dashboard.css` (the layout moves to `widgets/`), and a Phase 2 widget only where wrapping needs
  a prop (say which and why)
- `frontend/src/app/tokens.css` (new tokens only)
- `frontend/src/lib/api.ts` (a `GET /me` read for the catalog, if none exists)
- `docs/tasks/UI-3.1.report.md` (new)

No new dependency. Never use `pkill -f` or `killall`; stop only the processes you started, by
PID. `frontend/node_modules` is a symlink to the main checkout's: never `npm install`.

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into
`docs/tasks/UI-3.1.report.md` (AGENTS.md §6 format), with the PNG list. Do not commit.
