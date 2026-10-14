# Task UI-2.2 — Home: the KPI row (four cards)

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**. Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative) and
`frontend/src/app/tokens.css`. This is a SOC analyst's tool: judge every pixel against a
professional observability product, not a template dashboard.

## Binding design

[`docs/UI-REDESIGN-PLAN.md`](../UI-REDESIGN-PLAN.md):
- **UR.4.2**, card by card. The four cards are an allowed exception to "no row of identical KPI
  cards" **only if** each card (a) has a different structure, (b) carries a real breakdown,
  (c) drills down, and (d) holds no vanity number. Build exactly that, or the exception does not
  hold;
- **UR.4.1** (titles say what and for what window; units; tabular figures; UTC with the zone;
  no delta without a baseline) and **UR.4.4** (the state table);
- **D-11** (`docs/PLANNER.md` §4): dark only, the palette's original values.

**The data layer exists** (UI-2.1, merged): `frontend/src/hooks/useApiResource.ts` and the pure
derivations in `frontend/src/lib/home/` (`totalEps.ts`, `logSources.ts`, `offenses.ts`,
`anomalies.ts`, `qualifiers.ts`), with `homeReads` in `frontend/src/lib/api.ts`. Read
`docs/tasks/UI-2.1.report.md` (its open points say what the API lacks). **Use the derivations;
do not re-derive in a component.** If a derivation lacks something a card needs, extend it in
`lib/home/` with tests.

## Build

`frontend/src/components/home/`:
- `KpiTotalEps.tsx`, `KpiLogSources.tsx`, `KpiOffenses.tsx`, `KpiAnomalies.tsx`, each with its
  test;
- `KpiRow.tsx`: the four in a row; at 1024 px they may wrap to two by two, never overflow;
- `kpi.css` (new), imported by the components (as `components/ui/controls.css` is). **Every value
  is a token** from `tokens.css`; add a token there only when no existing one fits, in that file's
  voice.

Per card, what the plan specifies, plus:
- **Total EPS**: B-2.1 has landed, so the card gets its **sparkline** from
  `GET /fleet/eps-series?window=24h&group_by=none` (`backend/app/schemas/fleet.py`,
  `FleetEpsSeries`). Add the response types and a `homeReads.epsSeries(window, groupBy)` read to
  `api.ts`. The sparkline is a neutral line, no fill, no gradient, no marker on every point;
  **null steps are gaps, never connected and never drawn as zero**. Keep it small and quiet: it
  is context, not the headline. Draw it as inline SVG (the `HealthDistribution` precedent), not
  ECharts. It carries an accessible text equivalent (for example "24 h, 268 of 288 steps measured").
- **Log sources**: reuse `HealthDistribution` (find it under `components/`) for the breakdown bar,
  as UR.4.2 says.
- **Offenses**: the "no snapshot yet" case is `NOT_COLLECTED` (the derivation already decides it);
  the card says so in words.
- **Anomalies**: the severity breakdown says `TRUNCATED` ("first 200 of n") when it is.
- Each card is a link target or contains one link to its drill-down (UR.4.2 names each); the
  whole card need not be clickable, but the drill-down must be obvious and keyboard-reachable.
- Each card renders every state it can be in: loading (plain text, polite live region, no
  spinner), error (what failed and what to do, never the exception text), forbidden ("Your role
  cannot read … (permission …)"), the honesty qualifiers, and a failed refresh that keeps the old
  data (`refreshFailed` from `useApiResource`: say "Refresh failed; showing data received at
  HH:MM UTC").

## Tests (Vitest)

Each card: ok; every state above; null shown as an em dash with its reason, never `0`; the
drill-down href; units present. The sparkline: gaps for null steps (assert the path breaks), no
point drawn for a null, the text equivalent. Break the code on purpose for at least five guards
and confirm a test fails; list them.

## Screenshots (required)

The cards are not on a page yet (UI-2.5 composes `/`). For the capture only, create a **temporary**
route (for example `frontend/src/app/preview-kpi/page.tsx`) that renders `KpiRow` against a
deterministic fixture API, capture it, and **delete the route before you report**: it must not be
in your final diff. Keep the fixture server and capture script outside the repository, in
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui22/`.
The previous UI task's tooling is a usable starting point:
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui15b/`
(a fixture API in Node and a headless `/usr/bin/google-chrome` capture driver). Capture at
1440×900, 1280×800 and 1024×768: **ok**, **partial / insufficient data**, **no offense snapshot**,
**forbidden on one card**, **backend down**. Look at every PNG yourself before reporting; list the
paths.

## Allowed files

- `frontend/src/components/home/Kpi*.tsx` and their tests, `frontend/src/components/home/kpi.css`
- a sparkline component in `frontend/src/components/home/` and its test
- `frontend/src/lib/home/*.ts` (extensions, with tests)
- `frontend/src/lib/api.ts` (the eps-series types and read only)
- `frontend/src/app/tokens.css` (new tokens only, if needed)
- the temporary preview route (created and deleted)
- `docs/tasks/UI-2.2.report.md` (new)

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npm test`, `npm run build`. Copy each summary line verbatim into the report.

Write `docs/tasks/UI-2.2.report.md` in the AGENTS.md §6 format. Do not commit.
