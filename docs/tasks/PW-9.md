# Task PW-9 — Phase G widget: `chart.property_watch`

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a above all** (every
Dashboard widget state, including stale and forbidden naming the permission; the widget links to
the route that owns its data in every state). Then read `docs/PRODUCT-UX-GUIDELINES.md` §8
(`WidgetFrame`) and §11, `frontend/src/app/tokens.css`, and `docs/PHASE-G-PROPERTY-WATCH.md`
**§G.13.5** (binding) and §G.8.0's reading table.

The API (PW-5) and PW-7's series chart (`frontend/src/components/propertywatch/`) are merged:
reuse the chart, extend it only by props. **No backend change**: if the widget needs one, stop and
report.

## Build

- The registry entry `chart.property_watch` in `frontend/src/widgets/registry.ts` on the
  `chart.search_results` precedent: `requires: ["read:property_watches"]`,
  `sources: ["/property-watches/{id}/series"]`, `config: { watch_id }` chosen through an option
  source over `GET /property-watches` in `frontend/src/widgets/optionSources.ts` (pickers over ids,
  never free text), drill-down `/behavior/watches/{id}` in every state.
- It draws the pinned values and the top values of the last bucket, at most five, in
  `--chart-cat-1`…`--chart-cat-5`, the unit on the axis, UTC time of the last bucket, and the
  coverage pill.
- Every state of the §G.13.5 table: `loading`, `ok`, `ok` + `PARTIAL`/`TRUNCATED` ("at least n"),
  `stale` (older than twice the family cadence, with time and age), `empty` (why, and when it was
  created), `no_data` (`NOT_APPLICABLE`, with coverage), `not_collected` (naming the setting or
  the scope reason), `error` (what failed, previous answer kept), `forbidden` (naming
  `read:property_watches` and who grants it).
- `frontend/src/widgets/permissions.ts` lists the permission if its contract test needs it.

Values are attacker-controlled text: `echarts.format.encodeHTML` in tooltips, text elsewhere
(T-117); pin it with a test.

## Tests

Vitest for each state, the drill-down in every state, the option source, the gap rules inherited
from the chart (no zero at an unknown bucket, a measured zero as 0), at most five series, the
registry contract tests. Break-it each and report which failed. `colourLiterals.test.ts` and the
contrast tests stay green.

## Screenshots

A dashboard holding the widget in `ok`, `PARTIAL`, `stale`, `empty`, `not_collected` and
`forbidden`, at 1440×900, 1280×800 and 1024×768 with headless Chrome (`/usr/bin/google-chrome`)
against a deterministic fixture API with the **real** payload shapes. Fixture, script, PNGs and
scratch under `/home/efe/Documents/QRadar-Dash-work/pw9/`, never `/tmp`. Ports 3119/8119/9119.
Open the PNGs and say what you saw; list their paths. No page-level horizontal overflow.

## Allowed files

- `frontend/src/widgets/registry.ts`, `optionSources.ts`, `permissions.ts` (the PW entry only)
- new widget files under `frontend/src/widgets/` and their tests
- `frontend/src/components/propertywatch/**` (props only, no behaviour change for PW-7's pages)
- `docs/PHASE-G-PROPERTY-WATCH.md` `[BUILT — PW-9]` marker in §G.13.5; `docs/tasks/PW-9.report.md`

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm ci`, `npm run lint`,
`npm run typecheck`, `npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim.
Stop every process you start by PID. Do not commit.
