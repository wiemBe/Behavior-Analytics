# Task UI-5.4 — the fleet pages: review against the redesign and fix

Read `AGENTS.md` at the repository root first and follow it exactly, §3a above all. Then read
`docs/PRODUCT-UX-GUIDELINES.md` (normative: §4, §6, §8, §9, §10), `frontend/src/app/tokens.css`,
`frontend/src/components/ui/controls.css`, and in `docs/UI-REDESIGN-PLAN.md`: **UR.1** (its "Rule
for every entry" and the `/fleet`, `/fleet/sources`, `/fleet/catchall` entries of **UR.1.2**),
**UR.2** (tokens, severity scale, typography, chart palette), **UR.3.3** (page header),
**UR.7 Phase 5**, **UR.8.1** (test churn) and **UR.8.6** (1024×768). Read
`docs/tasks/UI-5.7.md` and its report: the same method, one phase earlier.

## What "restyle" means here

Phase 1 moved every page to the new tokens, typography, shell and shared components. These pages
were written against the old navy tokens. This task **looks at them closely, finds where they fall
short of the redesign and the guidelines, and fixes that in CSS and minimal markup.** It changes no
behaviour: no request, no permission rule, no copy that a test asserts.

**The orchestrator's note on UI-5.7:** its review found five defects in eight routes, all about
measure and spacing. Look harder here. Compare each page with the Home and the anomaly pages,
which were built on the new system: the same kind of thing (a table, a filter bar, a chart, a
status) must look and behave the same way. Inconsistency between pages is a defect.

## Routes

`/fleet` (each window `1h`, `24h`, `7d`), `/fleet/sources` (with filters applied, an empty
result, and a source in conflict), `/fleet/catchall` (a sender queue with pending and reviewed
senders, a catch-all source volume chart with measured, unmeasured and `LOWER_BOUND` buckets).

## Method

1. **Capture first.** A deterministic fixture API (below); every route and state at 1440×900,
   1280×800 and 1024×768 **before any change**, into `…/ui54/png/before/`.
2. **Review.** A defect list in the report, one row per defect: route, viewport, what is wrong,
   the rule it breaks (§3a item or guideline section), the fix. At least:
   - page overflow (§9), and tables that overflow instead of scrolling inside their container;
   - the shared `PageHeader` (UR.3.3); descriptions that restate the heading;
   - literals outside `tokens.css`, inline styles, navy-era values. **`/fleet/catchall` keeps about
     122 lines of CSS in `app/globals.css`, written against the navy tokens** (UR.1.2): re-read
     every rule against the new tokens and move them into a stylesheet colocated with the page;
   - numbers: unit, tabular figures, right-aligned in tables; times with a timezone;
   - charts: a unit on the axis, no marker on every point, no library-default styling, the chart
     palette of UR.2.6, unmeasured buckets shaded and never drawn as zero, `LOWER_BOUND` with the
     warn fill and the words "at least" (UR.1.2);
   - states: loading, empty (what is empty and why), error (what failed, what to do), and the
     honesty states UR.1.2 lists (a figure with no complete bucket is `null`, not zero;
     `auto_discovered` null = unknown; `review_status` null = no classification row);
   - filters and forms: labels, focus order, visible focus, `aria-describedby` on errors,
     consistent control sizes with the rest of the app;
   - the window switch (`WindowSwitch`) against the Home's range control: same look and keyboard
     behaviour, or a stated reason why not.
3. **Fix** each defect within the allowed files. Pinned classes stay (UR.8.1): change their CSS,
   never rename them. Keep the DOM order the page tests pin.
4. **Capture after**, the same set, into `…/ui54/png/after/`. Look at every PNG and say per route
   what changed.

## Tests

- All existing tests stay green **with no edits**. If a fix needs a test edit, it is the wrong fix;
  if a test seems wrong, stop and report it.
- Add tests for markup you add (an `aria-describedby`, a designed empty state that did not
  exist). **Break-it** each: confirm it fails, then restore.

## Screenshots and fixture

- Fixture API in `/home/efe/Documents/QRadar-Dash-work/ui54/fixture/` (outside the repository),
  serving the routes UR.1.2 lists for these pages with the **backend's real payload shapes** (read
  `backend/app/schemas/` for fleet, classification and catch-all; the `fixtures.ts` files under
  `app/fleet/catchall/` show shapes the tests use). The Home fixture at
  `/home/efe/Documents/QRadar-Dash-work/ui33b/fixture/` already serves `/fleet/summary`; copy from
  it, never edit it.
- Ports 3146/8146/9146. Headless Chrome is `/usr/bin/google-chrome`.
- PNGs under `/home/efe/Documents/QRadar-Dash-work/ui54/png/{before,after}/`. Nothing under
  `/tmp`.

## Allowed files

- `frontend/src/app/fleet/**`, `frontend/src/components/fleet/*`
- new stylesheets colocated with those files
- `frontend/src/app/globals.css`: **only** removing the `/fleet/catchall` rules you move out
- `frontend/src/app/tokens.css` (new tokens only, each justified in the report)
- `docs/tasks/UI-5.4.report.md` (new)

Not `components/ui/*` or other pages: if a shared component is the cause, list it as an open
point.

## Gates

From `frontend/` with `~/.local/node/bin` on `PATH`: `npm run lint`, `npm run typecheck`,
`npx vitest run --no-cache`, `npm run build`. Copy each summary line verbatim into the report
(AGENTS.md §6 format), with the defect list, the before/after PNG lists and what changed per
route. Do not commit. Stop every process you start, by PID. Never `pkill -f` or `killall`.
