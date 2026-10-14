# Task UI-2.4 — Home: the tables and the log-source health widget

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**. Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative) and
`frontend/src/app/tokens.css`. This is a SOC analyst's tool: judge it against a professional
observability product, not a template dashboard.

## Binding design

[`docs/UI-REDESIGN-PLAN.md`](../UI-REDESIGN-PLAN.md):
- **UR.4.3**: "Top log sources by EPS", "Log source health" (its category table: show **only**
  the categories with real detection logic; "Degraded" is not shown), "Recent anomalies" and
  "Recent offenses";
- **UR.4.1** (titles say what and for what window; units; tabular figures right-aligned; UTC
  with the zone and a `<time datetime>`; the age in muted text; a drill-down on every widget) and
  **UR.4.4** (the state table);
- **D-11** (`docs/PLANNER.md` §4): dark only, the palette's original values.

**The data layer exists** (UI-2.1, merged): `frontend/src/hooks/useApiResource.ts`, the pure
derivations in `frontend/src/lib/home/` (`topSources.ts`, `logSources.ts`, `anomalies.ts`,
`qualifiers.ts`), and `homeReads` in `frontend/src/lib/api.ts`. Read
`docs/tasks/UI-2.1.report.md`, especially its open points 4 and 5 (top-source EPS is a lower
bound when the class window is incomplete; the deviation compares the latest bucket). **Use the
derivations; do not re-derive in a component.** Extend `lib/home/` with tests where a widget needs
more.

Reuse what exists: `HealthDistribution` (`components/behavior/`), `healthDistribution()`
(`lib/behaviorOverview.ts`), `detectorLabel()` (`lib/behavior.ts`), the existing state pills and
table styles. Do not restyle them.

## Build

`frontend/src/components/home/`:
- `TopSources.tsx`: five rows; EPS with its unit and, where the derivation says so, the lower-bound
  marker in words; the deviation (em dash when null) labelled with its basis; the state word with
  its tone, not a bare dot; a **magnitude bar plus the number** for EPS (SVG or a token-styled
  element, no inline styles); row → `/behavior/sources/{id}`; "View all" → `/behavior/sources`.
- `RecentAnomalies.tsx`: as UR.4.3; the badge says "n active" from the response `total`, never
  "new"; the empty state names how many sources are not judged.
- `RecentOffenses.tsx`: as UR.4.3; magnitude as a **10-segment SVG bar and the number**, `null`
  → em dash and no bar; `NOT_COLLECTED` when there is no snapshot.
- `LogSourceHealth.tsx`: `HealthDistribution` plus the real categories of the UR.4.3 table, each
  with its count and a link where a page owns it.
- `tables.css` (new), imported by these components. **Every value is a token**; add a token to
  `tokens.css` only when none fits.

Every widget renders: loading (plain text, polite live region, no spinner), empty (what is empty
and why), error (what failed and what to do, never the exception text), forbidden ("Your role
cannot read … (permission …)"), the honesty qualifiers that apply, and a failed refresh that keeps
the old data (`refreshFailed` from `useApiResource`).

Tables: numbers right-aligned with tabular figures; long source names truncate with an ellipsis
and keep the full name in a `title` (they must not wrap to two 30 px lines); no table overflows its
widget at 1024 px.

## Tests (Vitest)

Each widget: ok; each state above; null → em dash, never `0`; the drill-down hrefs; time cells
carry the zone and a `datetime` attribute; the magnitude bar draws no segment for `null`. Break the
code on purpose for at least five guards and confirm a test fails; list them.

## Screenshots (required)

The widgets are not on a page yet (UI-2.5 composes `/`). For the capture only, create a
**temporary** route (for example `frontend/src/app/preview-tables/page.tsx`) that renders the four
widgets against a deterministic fixture API, capture it, and **delete the route before you
report**: it must not be in your final diff. Keep the fixture server and capture script outside
the repository, in
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui24/`.
A usable starting point is the previous UI task's tooling in
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui15b/`.
Capture at 1440×900, 1280×800 and 1024×768: **ok**, **empty**, **no offense snapshot**,
**forbidden on one widget**, **backend down**, and a fixture with a **long source name**. Look at
every PNG yourself before reporting; list the paths.

Another agent is building the KPI row (UI-2.2) in parallel in `components/home/Kpi*.tsx` and
`kpi.css`. Do not touch those files.

## Allowed files

- `frontend/src/components/home/{TopSources,RecentAnomalies,RecentOffenses,LogSourceHealth}.tsx`
  and their tests, `frontend/src/components/home/tables.css`
- `frontend/src/lib/home/*.ts` (extensions, with tests)
- `frontend/src/app/tokens.css` (new tokens only, if needed)
- the temporary preview route (created and deleted)
- `docs/tasks/UI-2.4.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npm test`, `npm run build`. Copy each summary line verbatim into the report.

Write `docs/tasks/UI-2.4.report.md` in the AGENTS.md §6 format. Do not commit.
