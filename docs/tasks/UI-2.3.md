# Task UI-2.3 — Home: the three charts (fleet EPS, EPS by source class, anomaly severity)

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**: no library-default chart style, no marker on every point, no rainbow series, a
unit on every axis, no pie or donut with more than five slices, expected ranges never drawn as
three lines, and a zero never drawn where a measurement is missing. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative) and `frontend/src/app/tokens.css`.

## Binding design

[`docs/UI-REDESIGN-PLAN.md`](../UI-REDESIGN-PLAN.md):
- **UR.4.3**: "Observed vs expected EPS" (the fail-closed rule, and "The expected band at fleet
  level does not exist"), "EPS by source class", "Anomaly severity distribution";
- **UR.2.4** (severity scale) and **UR.2.6** (chart palette); **UR.4.1** and **UR.4.4**;
- **D-11** (`docs/PLANNER.md` §4).

**Data that exists:**
- `GET /fleet/eps-series` (B-2.1): read `backend/app/fleet/series.py`, its schema in
  `backend/app/schemas/fleet.py`, and `docs/tasks/B-2.1.report.md`. A step's `eps` is `null`
  unless every in-scope cell is `COMPLETE`; `expected_eps` is a **sum of per-source medians, not a
  band**, and is `null` unless every in-scope source has a reliable cell. The window ends on a step
  boundary. UI-2.2 added its types and `homeReads.epsSeries` to `frontend/src/lib/api.ts`: use
  them.
- The pure derivations in `frontend/src/lib/home/` (UI-2.1): `epsByClass.ts`, `severityPerHour.ts`,
  `qualifiers.ts`; and `useApiResource` in `frontend/src/hooks/`. Use them; extend them with tests
  rather than re-deriving in a component.
- The chart conventions already in the product: `components/behavior/VolumeChart.tsx` (modular
  ECharts registration, `readChartPalette` from `lib/chartPalette.ts`, gap shading for unobserved
  intervals, tooltips with words) and `components/waf/WafRequestChart.tsx`. Match them: same
  registration pattern, same palette source, same gap treatment, same tooltip voice.

## Build

`frontend/src/components/home/`:

1. **`EpsChart.tsx`**: "Fleet EPS, {window}" over `GET /fleet/eps-series?window=…&group_by=none`.
   - Observed EPS as one line. **A null step is a gap**: not connected, not zero, and shaded as
     an unmeasured interval the way `VolumeChart` shades one; the tooltip on a gap says
     "n of m source-intervals measured".
   - Where `expected_eps` is present, one dashed line labelled **"Sum of per-source expected"**.
     **No band**, no `p05`/`p95`. The legend and an accessible note say what the line is.
   - Window presets `1h`, `24h`, `7d` (the existing segmented-control pattern on `/fleet`), y axis
     in "EPS", x axis in UTC with the zone shown.
   - States: loading, error, forbidden, `NOT_COLLECTED` (no measured step), `PARTIAL` (some steps
     null: "n of m steps measured"), the failed-refresh state from `useApiResource`.
   - Drill-down: the title links to `/fleet`.
2. **`EpsByClass.tsx`**: the donut with **at most five slices** from `epsByClass.ts`
   (`--chart-cat-1…4` and `--chart-other`, a 2 px canvas gap between slices, the total EPS in the
   centre), **and a list beside it** with class, EPS and share, which is the text carrier (the
   donut is `aria-hidden`). Classes with no mean are counted in words, never added as zero. When
   "Other classes" holds classes, the list says which. Slice or row → `/fleet/sources?class=…`.
   States: `INSUFFICIENT_DATA` when no class has a mean; the `PARTIAL` coverage line.
3. **`SeverityChart.tsx`**: stacked columns per hour from `severityPerHour.ts`, `--sev-*` tokens,
   a legend with the words, y axis "anomalies per hour". **`TRUNCATED`** ("first 200 of n") is
   shown, and the chart does not present a capped stack as the whole. A column links to
   `/anomalies?since&until&severity` where the anomalies page supports those parameters (check it;
   if it does not, link to `/anomalies` and say so in the report).
4. `charts.css` (new), imported by these components. **Every value is a token**; add a token to
   `tokens.css` only when none fits. No inline styles: an ECharts option object is not a style,
   but its colours come from `readChartPalette`, never a literal.

ECharts instances: created on mount, disposed on unmount, resized with their container; no
animation beyond what `VolumeChart` uses.

## Tests (Vitest)

As `VolumeChart.test.tsx` does it (test the option builder as a pure function, plus the
component's states): a null step is not connected and is shaded; no marker on every point; the
expected line is dashed, labelled, and absent where `expected_eps` is null; no band series exists;
the donut never has more than five data items; null classes never appear as zero; the severity
stack uses the severity tokens in a fixed order; `TRUNCATED` shows; every state renders. Break the
code on purpose for at least five guards and confirm a test fails; list them.

## Screenshots (required)

The charts are not on a page yet (UI-2.5 composes `/`). For the capture only, create a
**temporary** route (for example `frontend/src/app/preview-charts/page.tsx`) that renders the three
charts against a deterministic fixture API, capture it, and **delete the route before you
report**: it must not be in your final diff. Keep the fixture server and capture script outside
the repository, in
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui23/`;
the tooling in `…/scratchpad/ui15b/` or `…/scratchpad/ui22/` is a usable start. Use **port 3123**
for Next and **8123** for the fixture API. Capture at 1440×900, 1280×800 and 1024×768: **ok** (with
some null steps and an expected line), **all steps null** (`NOT_COLLECTED`), **no reliable baseline**
(no expected line), **truncated severity**, **one forbidden**, **backend down**. Look at every PNG
yourself before reporting; list the paths.

## Allowed files

- `frontend/src/components/home/{EpsChart,EpsByClass,SeverityChart}.tsx` and their tests, any
  option-builder modules beside them and their tests, `frontend/src/components/home/charts.css`
- `frontend/src/lib/home/*.ts` (extensions, with tests)
- `frontend/src/app/tokens.css` (new tokens only, if needed)
- the temporary preview route (created and deleted)
- `docs/tasks/UI-2.3.report.md` (new)

Do not touch `Kpi*.tsx`, `kpi.css`, `TopSources.tsx`, `RecentAnomalies.tsx`, `RecentOffenses.tsx`,
`LogSourceHealth.tsx` or `tables.css` (other tasks).

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npm test`, `npm run build`. Copy each summary line verbatim into the report.

Write `docs/tasks/UI-2.3.report.md` in the AGENTS.md §6 format. Do not commit.
