# Task UI-2.5 — the Home at `/`: composition, one widget frame, and the Phase 2 fix list

Read `AGENTS.md` at the repository root first and follow it exactly, **§3a (the frontend quality
bar) above all**. Then read `docs/PRODUCT-UX-GUIDELINES.md` (normative) and
`frontend/src/app/tokens.css`. The user wants this tool to look like a professional observability
product, not a template dashboard. **The first screen at 1440×900 answers "what needs me now".**

## Binding design

[`docs/UI-REDESIGN-PLAN.md`](../UI-REDESIGN-PLAN.md):
- **UR.4** (the composition, the widget rules UR.4.1 and the states UR.4.4), **UR.3.3** (the page
  header), the Phase 2 row for UI-2.5 in UR.7;
- **D-11** (`docs/PLANNER.md` §4): `/` is the Dashboard; `/behavior` stays the behavioral triage
  page; dark only; the palette's original values.

What is built and merged (read each report): UI-2.1 (`useApiResource`, `lib/home/*`), UI-2.2 (the
KPI row, `components/home/Kpi*.tsx`), UI-2.3 (the three charts), UI-2.4 (top sources, recent
anomalies, recent offenses, log-source health). **This task composes them; it does not redesign
them.** Fix only what the list below names.

## 1. Compose `/` (`frontend/src/app/page.tsx`)

Replace today's "SOC Overview" page (nine identical `StatCard`s over `/overview`) with the Home:

```
Page header: "Dashboard" + one line that is not a restatement of the title
KPI row (4)
Fleet EPS chart (8 cols)                     | EPS by source class (4 cols)
Top sources (4) | Anomaly severity (4)       | Log-source health (4)
Recent anomalies (6)                         | Recent offenses (6)
```

- A 12-column grid with fixed positions, from tokens (no new layout library; Phase 3 brings
  `react-grid-layout`). At 1280 the same grid; at **1024** it reflows (for example 6 + 6, then
  full width) with **no page overflow** and no widget narrower than its content allows (UI-2.4's
  report: recent anomalies and recent offenses need about 480 px).
- The Geography widget is **not** on the default layout yet (the map is a Phase 3 widget); do not
  add a placeholder.
- The widgets read through `useApiResource`, so the page is a client composition inside a server
  `page.tsx` shell (or however the existing pages split server and client: follow the pattern of
  the merged components). The shell's auto-refresh tick must refresh every widget.
- `frontend/src/app/loading.tsx` and `error.tsx` for `/` if the route needs them: plain text,
  polite live region, no spinner; the error says what failed and what to do.
- Report the page's data age to the top bar with `useReportDataAge` (UI-1.5) where a widget knows
  how far its data reaches (for example the fleet EPS series `window_end`).
- Remove whatever only the old page used, if nothing else imports it (check `StatCard`: other pages
  use it, so it stays).

## 2. The Phase 2 fix list

(a) **One widget frame.** UI-2.2's `KpiFrame.tsx` and UI-2.4's `WidgetParts.tsx` (and UI-2.3's, if
    it added one) duplicate the loading, forbidden, error, refresh-failed and qualifier states.
    Unify them into one `frontend/src/components/home/WidgetFrame.tsx` (UR.5.4's state set) and
    move every widget onto it. Behaviour and copy stay the same; the tests of each widget stay
    green (an import path in a test may change; no assertion may weaken).
(b) **`homeReads.recentOffenses`** in `api.ts` with an `AbortSignal`, replacing
    `recentOffensesRead` in `lib/home/recentOffenses.ts` (it cannot be aborted today).
(c) In Top sources at 1024, the "· Insufficient data" separator wraps to the start of a line: keep
    the separator with the word that follows it.
(d) `components/home/tables.css` restates the scroll-edge cover with `32px`/`14px` literals:
    move them to tokens (reuse the tokens `.table-scroll` in `globals.css` uses, if any; else add
    them).
(e) The Silent and Exceeding links in Log-source health go to `/anomalies?anomaly_type=…`. If the
    anomalies page supports an active-only parameter, add it; if not, say so in the report and leave
    the link.
(f) **One EPS format on the Home.** The KPI row prints EPS with `formatEpsValue` from
    `lib/fleet.ts` ("1,616.01 EPS", as `/fleet` does); the tables print it with `formatEps` from
    `lib/home/format.ts` ("2,477 EPS"). Use `formatEpsValue` everywhere on the Home (it is what
    `/fleet` prints, so the same source reads the same on both pages) and delete `formatEps` if
    nothing else uses it. Its tests go with it; say so.
(g) Uneven KPI card heights at 1440 (they are `align-items: start`): if the composed row looks
    ragged, align the cards' tops and let the row's height be the tallest card's, **without**
    stretching content or adding filler.

## Tests (Vitest)

`frontend/src/app/page.test.tsx`: the hierarchy (the page's `<h1>`, the widget headings in
order), each widget present, and the page-level states: **ok**, **empty** (a fresh install with no
data: every widget says what is empty and why, none shows `0` for an unmeasured figure),
**partial permission** (a principal that cannot read offenses: those widgets say so, the rest
render), **backend down**, **insufficient data**. The `WidgetFrame` test covers every state kind
once. Break the code on purpose for at least five guards and confirm a test fails; list them.

## Screenshots (required, the acceptance for all of Phase 2)

`/` at 1440×900, 1280×800 and 1024×768, with **ok**, **empty**, **partial permission**, **backend
down** and **insufficient data** fixtures. Use a deterministic fixture API outside the repository in
`/tmp/claude-1000/-home-efe-Documents-QRadar-Dash/1f0011eb-9918-4fba-a9e5-8fe15b30a127/scratchpad/ui25/`
(the fixtures under `…/scratchpad/ui22/`, `ui23/` and `ui24/` are a usable start); **port 3125** for
Next and **8125** for the fixture API. Capture the **first screen** (viewport only) and the full
page for each. Look at every PNG yourself; list them; state for each viewport what the first
screen shows.

## Allowed files

- `frontend/src/app/page.tsx`, `loading.tsx`, `error.tsx`, `page.test.tsx`
- `frontend/src/components/home/*` (the frame unification and the listed fixes only)
- `frontend/src/lib/home/*`, `frontend/src/lib/api.ts` (`homeReads.recentOffenses` only)
- `frontend/src/app/tokens.css` (new tokens only)
- `docs/tasks/UI-2.5.report.md` (new)

Never use `pkill -f` or `killall` with a broad pattern; stop only the processes you started.

## Gates

From `frontend/`, with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npm test`, `npm run build`. Copy each summary line verbatim into the report.

Write `docs/tasks/UI-2.5.report.md` in the AGENTS.md §6 format. Do not commit.
